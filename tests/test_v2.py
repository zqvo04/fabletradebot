"""v2 structural levers: playbook filter, full-entry sizing, kept-exit geometry."""
import numpy as np

from fabletradebot.config import test_config as make_cfg, v2_config
from fabletradebot.engine import Broker, Engine
from fabletradebot.risk import RiskManager
from fabletradebot.signals import Signal, generate


def _cfg_no_costs():
    cfg = make_cfg()
    cfg.fee_bps = 0.0
    cfg.slip_bps = {k: 0.0 for k in cfg.slip_bps}
    return cfg


def _engine(cfg):
    eng = Engine(cfg, Broker(cfg, 100_000.0), RiskManager(cfg))
    eng.positions["BTC"] = None
    return eng


def _arrays(bars, regime="SQUEEZE"):
    n = len(bars)
    o, h, l, c = (np.array([b[k] for b in bars], dtype=float) for k in range(4))
    return {
        "open": o, "high": h, "low": l, "close": c, "atr": np.ones(n),
        "e2raw": np.full(n, 0.2), "don_hi_f": np.full(n, 1e9),
        "don_lo_f": np.full(n, -1e9), "regime": np.array([regime] * n, dtype=object),
    }


def _p1_feature_arrays(n=40, regime="SQUEEZE"):
    """Minimal feature dict where bar n-1 is a clean Donchian-up breakout."""
    c = np.full(n, 100.0)
    c[-1] = 103.0
    A = {
        "open": np.full(n, 100.0), "high": c + 0.5, "low": c - 0.5, "close": c,
        "volume": np.full(n, 100.0), "atr": np.ones(n), "vol_sma": np.full(n, 50.0),
        "don_hi": np.full(n, 101.0), "don_lo": np.full(n, 95.0),
        "don_hi_f": np.full(n, 101.0), "don_lo_f": np.full(n, 96.0),
        "swing_hi": np.full(n, 101.0), "swing_lo": np.full(n, 95.0),
        "ema20": np.full(n, 100.0), "ema100": np.full(n, 99.0),
        "e2raw": np.full(n, 0.5), "f_z": np.full(n, np.nan),
        "bbw": np.full(n, 0.02), "bbw_pct": np.full(n, 10.0),
        "ret4h": np.zeros(n),
        "regime": np.array([regime] * n, dtype=object),
    }
    return A


def test_playbook_filter_disables_p2_p3():
    cfg = v2_config()
    assert cfg.playbooks == ("P1", "P4")
    A = _p1_feature_arrays(regime="TREND_UP")
    # in TREND_UP, only P2 could fire — with P2 disabled nothing may come out
    sigs = generate("BTC", A, len(A["close"]) - 1, cfg, None)
    assert all(s.playbook in cfg.playbooks for s in sigs)


def test_p1_fires_only_in_squeeze_by_default():
    cfg = v2_config()
    i = 39
    assert any(s.playbook == "P1"
               for s in generate("BTC", _p1_feature_arrays(regime="SQUEEZE"), i, cfg, None))
    assert not generate("BTC", _p1_feature_arrays(regime="CHOP"), i, cfg, None)


def test_full_entry_no_probe():
    """pyr_units=1: the whole planned size fills at entry and never adds."""
    cfg = _cfg_no_costs()
    cfg.pyr_units = 1
    eng = _engine(cfg)
    sig = Signal(asset="BTC", direction=1, playbook="P1", entry=100.0, stop=99.0,
                 z=0.8, horizon="swing")
    eng._open(sig, 30.0, i=0)
    pos = eng.positions["BTC"]
    pos.atr0 = 1.0
    assert abs(pos.qty - 30.0) < 1e-9
    # a strong advance must NOT pyramid
    bars = _arrays([(100, 100, 100, 100), (100.2, 100.9, 100.1, 100.8)])
    pos.scaled_out = True
    eng.manage("BTC", bars, 1, None, "CHOP")
    assert pos.unit == 1 and abs(pos.qty - 30.0) < 1e-9


def test_full_entry_stopout_costs_one_R():
    cfg = _cfg_no_costs()
    cfg.pyr_units = 1
    eng = _engine(cfg)
    sig = Signal(asset="BTC", direction=1, playbook="P1", entry=100.0, stop=99.0,
                 z=0.8, horizon="swing")
    eng._open(sig, 30.0, i=0)
    eng.positions["BTC"].atr0 = 1.0
    bars = _arrays([(100, 100, 100, 100), (100, 100.2, 98.5, 98.6)])
    eng.manage("BTC", bars, 1, None, "CHOP")
    assert eng.trades[-1]["reason"] == "stop"
    assert abs(eng.trades[-1]["r"] - (-1.0)) < 1e-9


def test_zero_partial_keeps_full_size_and_moves_breakeven():
    cfg = _cfg_no_costs()
    cfg.pyr_units = 1
    cfg.partial_frac = 0.0
    eng = _engine(cfg)
    sig = Signal(asset="BTC", direction=1, playbook="P1", entry=100.0, stop=99.0,
                 z=0.8, horizon="swing")
    eng._open(sig, 30.0, i=0)
    pos = eng.positions["BTC"]
    pos.atr0 = 1.0
    bars = _arrays([(100, 100, 100, 100), (100.2, 101.5, 100.0, 100.4)])
    eng.manage("BTC", bars, 1, None, "CHOP")
    assert pos.scaled_out                          # +1R event registered
    assert abs(pos.qty - 30.0) < 1e-9              # nothing sold
    assert abs(pos.stop - pos.avg_entry) < 1e-9    # breakeven move retained


def test_v2_config_defaults_frozen():
    cfg = v2_config()
    assert cfg.pyr_units == 1
    assert cfg.partial_frac == 0.0
    assert cfg.chandelier_atr == 3.25
    assert cfg.time_stop["P1"] == 8
    assert cfg.p1_regimes == ("SQUEEZE",)   # CHOP expansion rejected on holdout
    assert cfg.r_base == 0.0075
