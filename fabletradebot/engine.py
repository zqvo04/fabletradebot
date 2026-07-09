"""Strategy engine: probe-and-pyramid position lifecycle, exits, macro gate,
re-entry/cooldown, EV gate. Operates on per-asset dicts of numpy feature
arrays; order execution goes through a Broker (fees + slippage)."""
from dataclasses import dataclass, field

import numpy as np

from .config import Config
from .regime import TREND_UP, TREND_DOWN, CRISIS
from .signals import Signal, SWING, generate


class Broker:
    """Cash ledger with taker fees and per-asset slippage. Signed-qty accounting:
    equity = cash + sum(dir * qty * mark)."""

    def __init__(self, cfg: Config, equity0: float):
        self.cfg = cfg
        self.cash = equity0
        self.fees = 0.0
        self.funding = 0.0

    def fill(self, asset: str, side: int, qty: float, ref_price: float,
             kind: str = "taker") -> float:
        """side +1 buy / -1 sell. Maker fills: no slippage, maker fee."""
        if kind == "maker":
            px = ref_price
            fee = qty * px * self.cfg.maker_fee_bps * 1e-4
        else:
            px = ref_price * (1 + side * self.cfg.slip_bps[asset] * 1e-4)
            fee = qty * px * self.cfg.fee_bps * 1e-4
        self.cash -= side * qty * px + fee
        self.fees += fee
        return px

    def pay_funding(self, direction: int, qty: float, mark: float, rate: float):
        cost = direction * qty * mark * rate  # longs pay positive funding
        self.cash -= cost
        self.funding += cost

    def equity(self, positions: dict, marks: dict) -> float:
        eq = self.cash
        for asset, pos in positions.items():
            if pos is not None:
                eq += pos.direction * pos.qty * marks[asset]
        return eq


@dataclass
class Position:
    asset: str
    direction: int
    playbook: str
    horizon: str
    entry1: float                 # first (probe) fill price
    stop: float
    initial_stop: float
    full_qty: float               # planned full size
    qty: float                    # live size
    avg_entry: float
    risk_amount: float            # full_qty * |entry1 - initial_stop|
    r_denom: float                # |entry1 - initial_stop| (1R in price)
    atr0: float
    z: float
    opened_i: int
    targets: list = field(default_factory=list)
    risk_frac: float = 0.0        # risk_amount / equity at entry (for Monte Carlo)
    unit: int = 1                 # pyramid units filled (1..3)
    scaled_out: bool = False      # 1R partial (or P3 target-1) done
    best_px: float = 0.0          # favorable extreme since entry
    cash_flow: float = 0.0        # net cash incl. fees; == realized pnl at close

    def mfe(self) -> float:
        return self.direction * (self.best_px - self.entry1)


@dataclass
class Cooldown:
    consec: int = 0
    last_dir: int = 0
    last_z: float = 0.0
    last_i: int = -10**9
    until: int = -10**9


class Engine:
    def __init__(self, cfg: Config, broker: Broker, risk):
        self.cfg = cfg
        self.broker = broker
        self.risk = risk
        self.positions: dict[str, Position | None] = {}
        self.cooldowns: dict[str, Cooldown] = {}
        self.pending: dict[str, dict] = {}  # realistic maker entries awaiting a touch
        self.trades: list[dict] = []

    # ---------------- fills ----------------

    def _fill(self, pos: Position, side: int, qty: float, ref_px: float,
              kind: str = "taker") -> float:
        px = self.broker.fill(pos.asset, side, qty, ref_px, kind)
        rate = self.cfg.maker_fee_bps if kind == "maker" else self.cfg.fee_bps
        pos.cash_flow += -side * qty * px - rate * 1e-4 * qty * px
        return px

    def _entry_kind(self, playbook: str) -> str:
        if self.cfg.maker_entries != "none" and playbook in self.cfg.maker_entry_playbooks:
            return "maker"
        return "taker"

    def _exit_kind(self, resting: bool) -> str:
        """resting=True for target/partial levels that would be resting limits."""
        return "maker" if (resting and self.cfg.maker_exits) else "taker"

    def _open(self, sig: Signal, full_qty: float, i: int):
        d = sig.direction
        pos = Position(
            asset=sig.asset, direction=d, playbook=sig.playbook, horizon=sig.horizon,
            entry1=sig.entry, stop=sig.stop, initial_stop=sig.stop,
            full_qty=full_qty, qty=0.0, avg_entry=0.0,
            risk_amount=full_qty * abs(sig.entry - sig.stop),
            r_denom=abs(sig.entry - sig.stop), atr0=0.0, z=sig.z,
            opened_i=i, targets=list(sig.targets),
        )
        px = self._fill(pos, d, full_qty / 3.0, sig.entry,  # probe = 1/3
                        kind=self._entry_kind(sig.playbook))
        pos.qty = full_qty / 3.0
        pos.avg_entry = px
        pos.entry1 = px
        pos.r_denom = abs(px - sig.stop)
        pos.risk_amount = full_qty * pos.r_denom
        pos.best_px = px
        self.positions[sig.asset] = pos

    def _add(self, pos: Position, ref_px: float):
        add = pos.full_qty / 3.0
        px = self._fill(pos, pos.direction, add, ref_px)
        pos.avg_entry = (pos.avg_entry * pos.qty + px * add) / (pos.qty + add)
        pos.qty += add
        pos.unit += 1

    def _reduce(self, pos: Position, frac: float, ref_px: float, kind: str = "taker"):
        qty = pos.qty * frac
        self._fill(pos, -pos.direction, qty, ref_px, kind)
        pos.qty -= qty

    def _close(self, pos: Position, ref_px: float, reason: str, i: int, ts,
               kind: str = "taker"):
        if pos.qty > 0:
            self._fill(pos, -pos.direction, pos.qty, ref_px, kind)
            pos.qty = 0.0
        r = pos.cash_flow / pos.risk_amount if pos.risk_amount > 0 else 0.0
        self.trades.append(dict(
            asset=pos.asset, playbook=pos.playbook, horizon=pos.horizon,
            direction=pos.direction, entry=pos.entry1, opened_i=pos.opened_i,
            closed_i=i, closed_ts=ts, bars=i - pos.opened_i, unit=pos.unit,
            pnl=pos.cash_flow, r=r, reason=reason, z=pos.z, risk_frac=pos.risk_frac,
        ))
        self.risk.record_trade(r)
        cd = self.cooldowns.setdefault(pos.asset, Cooldown())
        if reason == "stop" and pos.cash_flow < 0:
            cd.consec += 1
            cd.last_dir, cd.last_z, cd.last_i = pos.direction, pos.z, i
            if cd.consec >= self.cfg.reentry_max + 1:      # 3rd straight stop
                cd.until = i + self.cfg.cooldown_bars
                cd.consec = 0
        else:
            cd.consec = 0
        self.positions[pos.asset] = None

    # ---------------- per-bar position management ----------------

    def manage(self, asset: str, A: dict, i: int, ts, btc_regime: str):
        pos = self.positions.get(asset)
        if pos is None:
            return
        cfg, d = self.cfg, pos.direction
        o, h, l, c = A["open"][i], A["high"][i], A["low"][i], A["close"][i]
        atr_ = A["atr"][i]
        regime = A["regime"][i]

        # 1) protective stop — conservative: checked before targets
        if (d > 0 and l <= pos.stop) or (d < 0 and h >= pos.stop):
            px = o if d * (o - pos.stop) < 0 else pos.stop  # gap through -> open
            self._close(pos, px, "stop", i, ts)
            return

        pos.best_px = max(pos.best_px, h) if d > 0 else min(pos.best_px, l)

        # 2) profit-taking
        if pos.targets:                                   # P3/P4 fixed targets
            tgt = pos.targets[0]
            if d * (h if d > 0 else l) >= d * tgt:
                px = o if d * (o - tgt) > 0 else tgt
                if len(pos.targets) > 1:
                    self._reduce(pos, 0.5, px, self._exit_kind(True))
                    pos.targets.pop(0)
                    pos.stop = pos.avg_entry              # breakeven
                    pos.scaled_out = True
                else:
                    self._close(pos, px, "target", i, ts, self._exit_kind(True))
                    return
        elif not pos.scaled_out:                          # generic +1R partial
            level = pos.entry1 + d * cfg.partial_at_r * pos.r_denom
            if d * (h if d > 0 else l) >= d * level:
                px = o if d * (o - level) > 0 else level
                self._reduce(pos, cfg.partial_frac, px, self._exit_kind(True))
                pos.stop = pos.avg_entry                  # breakeven
                pos.scaled_out = True

        # 3) trailing (swing playbooks, once de-risked)
        if pos.horizon == SWING and (pos.scaled_out or pos.unit >= 2) and not np.isnan(atr_):
            trail = pos.best_px - d * cfg.chandelier_atr * atr_
            pos.stop = max(pos.stop, trail) if d > 0 else min(pos.stop, trail)

        # 4) pyramiding (evaluated at close)
        if pos.unit == 1 and d * (c - pos.entry1) >= cfg.pyr_advance_atr * pos.atr0 \
                and d * A["e2raw"][i] >= 0:
            self._add(pos, c)
        elif pos.unit == 2 and pos.horizon == SWING:
            brk = A["don_hi_f"][i] if d > 0 else A["don_lo_f"][i]
            if not np.isnan(brk) and d * (c - brk) > 0:
                self._add(pos, c)
                be = pos.avg_entry                        # whole position to breakeven
                pos.stop = max(pos.stop, be) if d > 0 else min(pos.stop, be)

        # 5) time stop & max hold
        bars = i - pos.opened_i
        if bars >= cfg.time_stop[pos.playbook] and pos.mfe() < cfg.time_stop_mfe * pos.r_denom:
            self._close(pos, c, "time_stop", i, ts)
            return
        if bars >= cfg.max_hold[pos.playbook]:
            self._close(pos, c, "max_hold", i, ts)
            return

        # 6) hostile regime flip (regime outranks the signal)
        hostile = regime == CRISIS \
            or (d > 0 and regime == TREND_DOWN) or (d < 0 and regime == TREND_UP)
        if asset not in cfg.majors and btc_regime == CRISIS:  # macro gate
            hostile = True
        if hostile:
            self._close(pos, c, "regime", i, ts)

    # ---------------- entries ----------------

    def check_pending(self, asset: str, A: dict, i: int, ts):
        """Realistic maker entries: fill the resting limit if this bar trades
        through it; expire stale or invalidated orders. A limit only fills
        when price comes back against the trade — adverse selection is real."""
        order = self.pending.get(asset)
        if order is None:
            return
        sig, full_qty = order["sig"], order["full_qty"]
        regime = A["regime"][i]
        if i > order["expire_i"] or regime == CRISIS \
                or self.positions.get(asset) is not None:
            del self.pending[asset]
            return
        d = sig.direction
        touched = A["low"][i] <= sig.entry if d > 0 else A["high"][i] >= sig.entry
        if touched:
            del self.pending[asset]
            self._open(sig, full_qty, i)
            self.positions[asset].atr0 = order["atr0"]
            self.positions[asset].risk_frac = order["risk_frac"]

    def try_enter(self, asset: str, A: dict, i: int, ts, btc_ctx: dict | None,
                  equity: float, marks: dict):
        if self.positions.get(asset) is not None:
            return
        cfg = self.cfg
        cd = self.cooldowns.setdefault(asset, Cooldown())
        if i < cd.until:
            return
        if btc_ctx is not None and btc_ctx["regime"] == CRISIS:
            return                                        # macro gate: no alt entries

        sigs = generate(asset, A, i, cfg, btc_ctx)
        if not sigs:
            return
        sig = max(sigs, key=lambda s: s.z)

        # macro gate: alt trend-following must not fight BTC's 4h direction
        if btc_ctx is not None and sig.playbook in ("P1", "P2") \
                and sig.direction * btc_ctx["ret4h"] < 0:
            return

        # re-entry discipline after recent stop-outs in the same direction
        size_mult = sig.size_mult
        if cd.consec >= 1 and sig.direction == cd.last_dir \
                and i - cd.last_i <= cfg.reentry_window:
            if sig.z <= cd.last_z:
                return
            size_mult *= cfg.reentry_decay ** cd.consec

        regime = A["regime"][i]
        if not self._ev_positive(sig, A, i):
            return
        full = self.risk.full_qty(equity, regime, asset, sig.entry, sig.stop, size_mult)
        if full <= 0:
            return
        if not self.risk.portfolio_ok(
                {a: (p.direction, p.qty) if p else None for a, p in self.positions.items()},
                asset, sig.direction, full * sig.entry, marks, equity):
            return
        risk_frac = full * abs(sig.entry - sig.stop) / equity if equity > 0 else 0.0
        if self.cfg.maker_entries == "realistic" \
                and sig.playbook in self.cfg.maker_entry_playbooks:
            self.pending[asset] = dict(  # rest a limit at the signal close
                sig=sig, full_qty=full, expire_i=i + self.cfg.pending_ttl,
                atr0=float(A["atr"][i]), risk_frac=risk_frac)
            return
        self._open(sig, full, i)
        self.positions[asset].atr0 = float(A["atr"][i])
        self.positions[asset].risk_frac = risk_frac

    def _ev_positive(self, sig: Signal, A: dict, i: int) -> bool:
        """EV = p*W - (1-p)*L - costs, all in R. Costs scale with entry/stop
        distance, which is what makes tight-stop trades fee-dominated."""
        cfg = self.cfg
        p, w = cfg.ev_pw[sig.playbook]
        dist = abs(sig.entry - sig.stop)
        if dist <= 0:
            return False
        notional_per_r = sig.entry / dist
        taker = cfg.fee_bps + cfg.slip_bps[sig.asset]
        entry_bps = cfg.maker_fee_bps if self._entry_kind(sig.playbook) == "maker" else taker
        cost_r = (entry_bps + taker) * 1e-4 * notional_per_r  # exit modeled as taker
        f = A["funding"][i]
        if not np.isnan(f):
            periods = cfg.exp_hold[sig.playbook] / 8.0
            cost_r += sig.direction * f * periods * notional_per_r  # negative if paid to us
        return p * w - (1 - p) * 1.0 - cost_r > 0

    def flatten_all(self, marks: dict, i: int, ts, reason: str):
        for asset, pos in list(self.positions.items()):
            if pos is not None:
                self._close(pos, marks[asset], reason, i, ts)
