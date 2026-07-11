"""Discrete directional trade engine — deterministic bar-close replay.

One pass over the merged 1H grid. At each bar-open time t:
  1. fill entries decided at the previous bar close (at this bar's open),
  2. manage open positions intrabar (SL first, then TP1 — conservative),
     apply funding at 8h settlements, trail/time/regime exits at the close,
  3. at the bar close, accept new candidates -> pending fills for t+1h.

The same function serves backtest and the live paper loop (replay-from-anchor),
so there is exactly one implementation of the trading rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Params, spec
from .risk import final_leverage, size_position


@dataclass
class Position:
    sym: str
    direction: int
    conf: float
    setup: str
    regime: str
    entry: float
    sl: float
    sl0: float
    tp1: float
    notional: float
    margin: float
    risk_amt: float
    leverage: float
    liq_price: float
    opened_ts: pd.Timestamp
    meta: dict = field(default_factory=dict)
    bars: int = 0
    tp1_done: bool = False
    best_close: float = 0.0
    realized: float = 0.0          # accumulated pnl in account currency
    bias_flip_streak: int = 0

    def remaining_notional(self) -> float:
        return self.notional * (0.5 if self.tp1_done else 1.0)

    def open_risk(self, p: Params) -> float:
        loss_frac = max(0.0, self.direction * (self.entry - self.sl) / self.entry)
        return loss_frac * self.remaining_notional()


@dataclass
class Pending:
    sym: str
    direction: int
    conf: float
    sl: float
    setup: str
    regime: str
    decided_ts: pd.Timestamp
    meta: dict = field(default_factory=dict)   # signal components for attribution


def _cost(notional: float, sym: str, p: Params) -> float:
    """Fee component only — slippage is charged inside fill/exit prices."""
    return notional * p.taker_fee * p.cost_mult


def run(frames: dict[str, pd.DataFrame], features: dict[str, pd.DataFrame],
        candidates: dict[str, pd.DataFrame], funding: dict[str, pd.Series],
        regime: pd.DataFrame, corr_alert: pd.Series, p: Params,
        start: pd.Timestamp | None = None, end: pd.Timestamp | None = None,
        equity0: float = 10_000.0) -> dict:
    grid = pd.DatetimeIndex(sorted(set().union(*[df.index for df in frames.values()])))
    if start is not None:
        grid = grid[grid >= start]
    if end is not None:
        grid = grid[grid <= end]

    bars = {s: df.reindex(grid) for s, df in frames.items()}
    atr1h = {s: features[s]["atr1h"].reindex(grid) for s in frames}
    bias4h = {s: features[s]["bias4h"].reindex(grid) for s in frames}
    cand_at = {s: {ts: row for ts, row in candidates[s].iterrows()} for s in candidates}
    fund_at = {s: dict(zip(funding[s].index, funding[s].values))
               for s in funding if funding[s] is not None}
    state_at = regime["state"].reindex(grid).fillna("RANGE")
    corr_at = corr_alert.reindex(grid).fillna(False)

    cash, peak = equity0, equity0
    dd_frozen = False
    circuit_until: pd.Timestamp | None = None
    loss_log: list[tuple[pd.Timestamp, float]] = []   # realized losses for 24h circuit
    cooldown: dict[str, int] = {}
    positions: dict[str, Position] = {}
    pendings: list[Pending] = []
    trades: list[dict] = []
    curve: list[tuple[pd.Timestamp, float]] = []

    def mtm(ts_prices: dict[str, float]) -> float:
        u = 0.0
        for s, pos in positions.items():
            px = ts_prices.get(s)
            if px is not None and not np.isnan(px):
                u += pos.direction * (px - pos.entry) / pos.entry * pos.remaining_notional()
            u += pos.realized
        return cash + u

    def close_part(pos: Position, px: float, frac_notional: float, ts, reason: str):
        nonlocal cash
        gross = pos.direction * (px - pos.entry) / pos.entry * frac_notional
        pos.realized += gross - _cost(frac_notional, pos.sym, p)

    def finalize(pos: Position, px: float, ts: pd.Timestamp, reason: str):
        nonlocal cash
        close_part(pos, px, pos.remaining_notional(), ts, reason)
        pnl = pos.realized - _cost(pos.notional, pos.sym, p)   # entry-side cost
        cash += pnl
        if pnl < 0:
            loss_log.append((ts, -pnl))
        r = pnl / pos.risk_amt if pos.risk_amt > 0 else 0.0
        price_pct = pos.direction * (px - pos.entry) / pos.entry * 100
        trades.append({
            "sym": pos.sym, "setup": pos.setup, "dir": pos.direction,
            "conf": round(pos.conf, 4), "leverage": pos.leverage,
            "regime": pos.regime, "entry": pos.entry, "sl0": pos.sl0,
            "exit": px, "opened": pos.opened_ts, "closed": ts,
            "bars": pos.bars, "r": r, "pnl": pnl,
            "pnl_pct_price": price_pct, "pnl_pct_lev": price_pct * pos.leverage,
            "reason": reason, "risk_amt": pos.risk_amt, "notional": pos.notional,
            "equity_after": cash, **pos.meta,
        })
        cooldown[pos.sym] = p.cooldown_bars
        del positions[pos.sym]

    for i, t in enumerate(grid):
        prices_now = {s: bars[s]["close"].iloc[i] for s in positions}
        state = state_at.iloc[i]

        # ---- 1. fill pending entries at this bar's open ----
        for pend in pendings:
            row = bars[pend.sym].iloc[i]
            if np.isnan(row["open"]) or pend.sym in positions:
                continue
            eq = mtm(prices_now)
            dd = 1 - eq / peak if peak > 0 else 0.0
            if dd_frozen and dd <= p.dd_resume:
                dd_frozen = False
            if dd >= p.dd_stop:
                dd_frozen = True
            recent_loss = sum(x for ts0, x in loss_log
                              if (t - ts0) <= pd.Timedelta(hours=24))
            if recent_loss >= p.circuit_loss_24h * eq:
                circuit_until = t + pd.Timedelta(hours=p.circuit_pause_h)
            if (dd_frozen or state == "CRISIS"
                    or (circuit_until is not None and t < circuit_until)):
                continue
            corr_on = bool(corr_at.iloc[i])
            max_pos = p.max_positions_corr if corr_on else p.max_positions
            if len(positions) >= max_pos:
                continue
            fill = row["open"] * (1 + pend.direction * spec(pend.sym).slippage * p.cost_mult)
            stop_frac = pend.direction * (fill - pend.sl) / fill
            if stop_frac <= 0:
                continue
            lev, risk_frac = final_leverage(pend.conf, stop_frac, pend.regime,
                                            spec(pend.sym).lev_cap, p)
            if lev == 0.0:
                continue
            mult = (0.5 if dd >= p.dd_half else 1.0) * (0.5 if corr_on else 1.0)
            sz = size_position(eq, risk_frac * mult, fill, pend.sl, pend.direction, lev)
            open_risk = sum(pos.open_risk(p) for pos in positions.values())
            open_margin = sum(pos.margin for pos in positions.values())
            if open_risk + sz.risk_amt > p.max_open_risk * eq:
                continue
            if open_margin + sz.margin > p.max_margin_frac * eq:
                continue
            tp1 = fill * (1 + pend.direction * p.tp1_r * stop_frac) \
                if p.tp1_r > 0 else 0.0
            positions[pend.sym] = Position(
                sym=pend.sym, direction=pend.direction, conf=pend.conf,
                setup=pend.setup, regime=pend.regime, entry=fill, sl=pend.sl,
                sl0=pend.sl, tp1=tp1, notional=sz.notional, margin=sz.margin,
                risk_amt=sz.risk_amt, leverage=sz.leverage, liq_price=sz.liq_price,
                opened_ts=t, meta=pend.meta, best_close=fill)
        pendings = []

        # ---- 2. manage open positions over bar t ----
        for sym in list(positions):
            pos = positions[sym]
            row = bars[sym].iloc[i]
            if np.isnan(row["open"]):
                continue
            pos.bars += 1
            d = pos.direction
            # funding settles at 00/08/16 UTC (bar-open instant); where history
            # is missing, charge the conservative default drag instead
            if t.hour % 8 == 0 and pos.opened_ts < t:
                rate = fund_at.get(sym, {}).get(t)
                if rate is not None:
                    pos.realized -= d * rate * pos.remaining_notional()
                else:
                    pos.realized -= (p.funding_default_drag * p.cost_mult
                                     * pos.remaining_notional())
            lo, hi, close_px = row["low"], row["high"], row["close"]
            # liquidation must be unreachable before the stop — hard invariant
            if (d == 1 and lo <= pos.liq_price) or (d == -1 and hi >= pos.liq_price):
                if (d == 1 and lo > pos.sl) or (d == -1 and hi < pos.sl):
                    raise AssertionError(f"liquidation before stop on {sym} at {t}")
            sl_hit = lo <= pos.sl if d == 1 else hi >= pos.sl
            tp_on = pos.tp1 > 0
            tp_hit = tp_on and (hi >= pos.tp1 if d == 1 else lo <= pos.tp1)
            if sl_hit:  # conservative: stop fills before any TP in the same bar
                px = pos.sl * (1 - d * spec(sym).slippage * p.cost_mult)
                finalize(pos, px, t, "SL" if pos.sl == pos.sl0 else "Trail")
                continue
            if tp_hit and not pos.tp1_done:
                close_part(pos, pos.tp1, pos.notional * p.tp1_frac, t, "TP1")
                pos.tp1_done = True
                pos.sl = pos.entry  # break-even stop for the runner
            # close-based management
            pos.best_close = max(pos.best_close, close_px) if d == 1 \
                else min(pos.best_close, close_px)
            a = atr1h[sym].iloc[i]
            if not np.isnan(a):   # chandelier trail, active from entry
                trail = pos.best_close - d * p.trail_atr * a
                if d * (trail - pos.sl) > 0:
                    pos.sl = trail
            unreal_r = (d * (close_px - pos.entry) / pos.entry * pos.notional
                        ) / pos.risk_amt
            b4 = bias4h[sym].iloc[i]
            pos.bias_flip_streak = pos.bias_flip_streak + 1 if b4 == -d else 0
            exit_px = close_px * (1 - d * spec(sym).slippage * p.cost_mult)
            if state == "CRISIS":
                finalize(pos, exit_px, t, "Regime")
            elif pos.setup == "BRK" and pos.bias_flip_streak >= 2:
                finalize(pos, exit_px, t, "BiasFlip")
            elif (p.time_stop_bars > 0 and pos.bars >= p.time_stop_bars
                  and unreal_r < p.time_stop_min_r):
                finalize(pos, exit_px, t, "Timeout")

        # ---- 3. decide at bar close: candidates -> pending fills for t+1h ----
        for sym in cand_at:
            if sym in positions:
                continue
            if cooldown.get(sym, 0) > 0:
                continue
            row = cand_at[sym].get(t)
            if row is None:
                continue
            meta = {k: float(row[k]) for k in
                    ("c_base", "c_fit", "c_align", "c_fund") if k in row.index}
            pendings.append(Pending(sym=sym, direction=int(row["dir"]),
                                    conf=float(row["conf"]), sl=float(row["sl"]),
                                    setup=str(row["setup"]), regime=state,
                                    decided_ts=t, meta=meta))
        for sym in list(cooldown):
            cooldown[sym] -= 1
            if cooldown[sym] <= 0:
                del cooldown[sym]

        prices_close = {s: bars[s]["close"].iloc[i] for s in positions}
        curve.append((t, mtm(prices_close)))

    eq_curve = pd.Series(dict(curve), name="equity")
    return {"trades": pd.DataFrame(trades), "equity": eq_curve,
            "open_positions": positions, "final_equity": cash}
