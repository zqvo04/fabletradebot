"""Engine mechanics: the FP/FN machinery must behave exactly as designed."""
import numpy as np

from fabletradebot.config import test_config as make_cfg
from fabletradebot.engine import Broker, Engine
from fabletradebot.risk import RiskManager
from fabletradebot.signals import Signal


def _cfg_no_costs():
    cfg = make_cfg()
    cfg.fee_bps = 0.0
    cfg.slip_bps = {k: 0.0 for k in cfg.slip_bps}
    return cfg


def _arrays(bars, regime="SQUEEZE"):
    """bars: list of (o, h, l, c) with constant atr=1 and neutral extras."""
    n = len(bars)
    o, h, l, c = (np.array([b[k] for b in bars], dtype=float) for k in range(4))
    return {
        "open": o, "high": h, "low": l, "close": c, "atr": np.ones(n),
        "e2raw": np.full(n, 0.2), "don_hi_f": np.full(n, 1e9),
        "don_lo_f": np.full(n, -1e9), "regime": np.array([regime] * n, dtype=object),
    }


def _engine(cfg):
    eng = Engine(cfg, Broker(cfg, 100_000.0), RiskManager(cfg))
    eng.positions["BTC"] = None
    return eng


def _open(eng, entry=100.0, stop=99.0, playbook="P1", horizon="swing",
          targets=(), full_qty=30.0):
    sig = Signal(asset="BTC", direction=1, playbook=playbook, entry=entry,
                 stop=stop, z=0.8, horizon=horizon, targets=list(targets))
    eng._open(sig, full_qty, i=0)
    eng.positions["BTC"].atr0 = 1.0
    return eng.positions["BTC"]


def test_probe_stopout_costs_one_third_R():
    """A false positive on the probe must cost ~0.33R, not 1R."""
    eng = _engine(_cfg_no_costs())
    _open(eng)  # probe = 10 units @100, stop 99, full risk = 30
    bars = _arrays([(100, 100, 100, 100), (100, 100.2, 98.5, 98.6)])
    eng.manage("BTC", bars, 1, None, "CHOP")
    t = eng.trades[-1]
    assert t["reason"] == "stop"
    assert abs(t["r"] - (-1.0 / 3.0)) < 1e-9


def test_partial_take_profit_and_breakeven():
    eng = _engine(_cfg_no_costs())
    pos = _open(eng)
    # high tags +1R (101) but close stays under the pyramid trigger (100.5)
    bars = _arrays([(100, 100, 100, 100), (100.2, 101.5, 100.0, 100.4)])
    eng.manage("BTC", bars, 1, None, "CHOP")
    assert pos.scaled_out and abs(pos.qty - 6.0) < 1e-9  # 40% off at +1R
    assert abs(pos.stop - pos.avg_entry) < 1e-9          # stop -> breakeven
    # scratch at breakeven: net pnl == the banked partial (4 units x 1.0)
    bars2 = _arrays([(0, 0, 0, 0), (0, 0, 0, 0), (100.0, 100.1, 99.5, 99.6)])
    eng.manage("BTC", bars2, 2, None, "CHOP")
    assert abs(eng.trades[-1]["pnl"] - 4.0) < 1e-9


def test_time_stop_kills_stagnant_trade():
    cfg = _cfg_no_costs()
    eng = _engine(cfg)
    _open(eng)  # P1 time stop = 12 bars, needs MFE < 0.5R
    flat = _arrays([(100, 100.1, 99.9, 100.0)] * 20)
    flat["e2raw"] = np.zeros(20)  # block pyramiding
    for i in range(1, 13):
        eng.manage("BTC", flat, i, None, "CHOP")
    assert eng.trades and eng.trades[-1]["reason"] == "time_stop"
    assert eng.trades[-1]["bars"] == 12


def test_pyramid_units_and_breakeven_on_third():
    eng = _engine(_cfg_no_costs())
    pos = _open(eng)
    pos.scaled_out = True  # isolate pyramiding from the +1R partial
    bars = _arrays([(100, 100, 100, 100),
                    (100.2, 100.7, 100.1, 100.6),   # +0.6 > 0.5*atr -> unit 2
                    (100.8, 102.2, 100.7, 102.0)])  # breaks don_fast -> unit 3
    bars["don_hi_f"] = np.array([1e9, 1e9, 101.5])
    eng.manage("BTC", bars, 1, None, "CHOP")
    assert pos.unit == 2 and abs(pos.qty - 20.0) < 1e-9
    eng.manage("BTC", bars, 2, None, "CHOP")
    assert pos.unit == 3 and abs(pos.qty - 30.0) < 1e-9
    assert pos.stop >= pos.avg_entry - 1e-9  # whole position de-risked


def test_hostile_regime_closes_position():
    eng = _engine(_cfg_no_costs())
    _open(eng)
    bars = _arrays([(100, 100, 100, 100), (100, 100.4, 99.8, 100.2)], regime="TREND_DOWN")
    eng.manage("BTC", bars, 1, None, "CHOP")
    assert eng.trades[-1]["reason"] == "regime"


def test_macro_gate_closes_alt_on_btc_crisis():
    eng = _engine(_cfg_no_costs())
    eng.positions["SOL"] = None
    sig = Signal(asset="SOL", direction=1, playbook="P2", entry=100.0, stop=99.0,
                 z=0.8, horizon="swing")
    eng._open(sig, 30.0, i=0)
    eng.positions["SOL"].atr0 = 1.0
    bars = _arrays([(100, 100, 100, 100), (100, 100.4, 99.8, 100.2)], regime="TREND_UP")
    eng.manage("SOL", bars, 1, None, "CRISIS")  # BTC in crisis
    assert eng.trades[-1]["reason"] == "regime"


def test_cooldown_after_three_straight_stops():
    cfg = _cfg_no_costs()
    eng = _engine(cfg)
    stop_bar = _arrays([(100, 100, 100, 100), (100, 100.2, 98.5, 98.6)])
    for k in range(3):
        _open(eng)
        eng.positions["BTC"].opened_i = 0
        eng.manage("BTC", stop_bar, 1, None, "CHOP")
    cd = eng.cooldowns["BTC"]
    assert cd.until == 1 + cfg.cooldown_bars  # 3rd stop arms the cooldown
    assert cd.consec == 0


def test_maker_fill_economics():
    """Maker fills: no slippage, maker fee; taker fills keep slip+taker fee."""
    cfg = make_cfg()
    cfg.slip_bps = {k: 10.0 for k in cfg.slip_bps}   # visible slippage
    cfg.maker_exits = True
    cfg.maker_entries = "optimistic"
    eng = _engine(cfg)
    pos = _open(eng, playbook="P3", targets=(101.0, 102.0))  # maker entry playbook
    assert abs(pos.entry1 - 100.0) < 1e-9            # no slip on maker entry
    # taker probe for P1 would slip: reset and compare
    eng2 = _engine(cfg)
    pos2 = _open(eng2, playbook="P1")
    assert pos2.entry1 > 100.0                        # buy slipped up 10bp


def test_realistic_pending_fills_only_on_touch():
    cfg = _cfg_no_costs()
    cfg.maker_entries = "realistic"
    eng = _engine(cfg)
    sig = Signal(asset="BTC", direction=1, playbook="P3", entry=100.0, stop=99.0,
                 z=0.9, horizon="day", targets=[101.0, 102.0])
    # emulate try_enter's pending registration path
    eng.pending["BTC"] = dict(sig=sig, full_qty=30.0, expire_i=3, atr0=1.0,
                              risk_frac=0.003)
    bars = _arrays([(0, 0, 0, 0),
                    (100.5, 100.8, 100.2, 100.6),   # never touches 100 -> no fill
                    (100.4, 100.5, 99.9, 100.1),    # touches 100 -> fill
                    (0, 0, 0, 0), (0, 0, 0, 0)])
    eng.check_pending("BTC", bars, 1, None)
    assert eng.positions["BTC"] is None
    eng.check_pending("BTC", bars, 2, None)
    assert eng.positions["BTC"] is not None
    assert abs(eng.positions["BTC"].entry1 - 100.0) < 1e-9


def test_realistic_pending_expires():
    cfg = _cfg_no_costs()
    cfg.maker_entries = "realistic"
    eng = _engine(cfg)
    sig = Signal(asset="BTC", direction=1, playbook="P3", entry=100.0, stop=99.0,
                 z=0.9, horizon="day")
    eng.pending["BTC"] = dict(sig=sig, full_qty=30.0, expire_i=2, atr0=1.0,
                              risk_frac=0.003)
    bars = _arrays([(101, 101, 100.5, 101)] * 5)
    eng.check_pending("BTC", bars, 3, None)           # past expire_i
    assert "BTC" not in eng.pending and eng.positions["BTC"] is None
