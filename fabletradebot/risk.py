"""Risk & Sizing Model: fractional-Kelly risk unit, dynamic multipliers,
portfolio caps, and non-negotiable circuit breakers."""
from collections import deque

import pandas as pd

from .config import Config

OK = "ok"
HALTED = "halted"       # daily stop tripped: flatten + no entries for halt window
HARD_STOP = "hard_stop"  # max-DD breached: flatten + no entries for the whole run


class RiskManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.recent_r = deque(maxlen=cfg.perf_win)
        self.hwm = None
        self.day_anchor = None
        self.day_key = None
        self.week_anchor = None
        self.week_key = None
        self.week_mult = 1.0        # next-week r_base reduction after a -7% week
        self.halted_until = None
        self.hard_stopped = False

    # ---------------- sizing ----------------

    def m_perf(self) -> float:
        if len(self.recent_r) < 5:
            return 1.0
        s = sum(self.recent_r)
        if s < self.cfg.perf_lo:
            return self.cfg.m_perf_lo
        if s > self.cfg.perf_hi:
            return min(self.cfg.m_perf_hi, self.cfg.m_perf_cap)
        return 1.0

    def r_eff(self, regime: str, asset: str) -> float:
        m_reg = self.cfg.m_regime.get(regime, 0.0)  # CRISIS/WARMUP -> 0
        return (self.cfg.r_base * self.week_mult * m_reg
                * self.m_perf() * self.cfg.m_liq[asset])

    def full_qty(self, equity: float, regime: str, asset: str,
                 entry: float, stop: float, size_mult: float = 1.0) -> float:
        """Planned FULL position size; probe entries take 1/3 of this."""
        dist = abs(entry - stop)
        if dist <= 0 or equity <= 0:
            return 0.0
        qty = self.r_eff(regime, asset) * equity * size_mult / dist
        qty = min(qty, self.cfg.lev_cap[asset] * equity / entry)  # leverage cap
        return max(qty, 0.0)

    def record_trade(self, r: float):
        self.recent_r.append(r)

    # ---------------- portfolio constraints ----------------

    def portfolio_ok(self, positions: dict, asset: str, direction: int,
                     add_notional: float, marks: dict, equity: float) -> bool:
        """positions: {asset: (direction, qty)} currently open."""
        cfg = self.cfg
        live = {a: p for a, p in positions.items() if p is not None}
        if asset not in live and len(live) >= cfg.max_positions:
            return False
        same_dir = sum(1 for a, (d, _) in live.items() if d == direction and a != asset)
        if asset not in live and same_dir >= cfg.max_same_dir:
            return False
        gross_beta = add_notional * cfg.beta[asset]
        for a, (_, q) in live.items():
            gross_beta += abs(q) * marks[a] * cfg.beta[a]
        return gross_beta <= cfg.beta_cap * equity

    # ---------------- circuit breakers ----------------

    def on_bar(self, ts: pd.Timestamp, equity: float) -> str:
        """Update anchors, return trading status for this bar."""
        cfg = self.cfg
        if self.hwm is None:
            self.hwm = equity
        self.hwm = max(self.hwm, equity)

        day = ts.normalize()
        if day != self.day_key:
            self.day_key, self.day_anchor = day, equity
        week = (ts.isocalendar().year, ts.isocalendar().week)
        if week != self.week_key:
            if self.week_key is not None and self.week_anchor > 0:
                week_ret = equity / self.week_anchor - 1.0
                self.week_mult = 0.5 if week_ret <= cfg.weekly_stop else 1.0
            self.week_key, self.week_anchor = week, equity

        if self.hard_stopped or equity / self.hwm - 1.0 <= cfg.mdd_stop:
            self.hard_stopped = True
            return HARD_STOP
        if self.halted_until is not None:
            if ts < self.halted_until:
                return HALTED
            self.halted_until = None
        if self.day_anchor > 0 and equity / self.day_anchor - 1.0 <= cfg.daily_stop:
            self.halted_until = ts + pd.Timedelta(hours=cfg.halt_bars)
            return HALTED
        return OK
