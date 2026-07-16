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

from dataclasses import asdict, dataclass, field

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
    entry: float                   # tranche-0 entry (records show weighted avg)
    sl: float
    sl0: float
    tp1: float
    notional: float                # tranche-0 notional
    margin: float
    risk_amt: float
    leverage: float
    liq_price: float
    opened_ts: pd.Timestamp
    risk_frac: float = 0.0         # per-unit risk fraction used at entry
    init_stop_frac: float = 0.0
    meta: dict = field(default_factory=dict)
    tranches: list = field(default_factory=list)   # [(entry_px, notional), ...]
    adds: int = 0
    bars: int = 0
    tp1_done: bool = False
    best_close: float = 0.0
    realized: float = 0.0          # accumulated pnl in account currency
    bias_flip_streak: int = 0
    fade_streak: int = 0           # consecutive bars of decayed hold_conf (winner)
    loss_fade_streak: int = 0      # consecutive bars of collapsed hold_conf (loser)
    hold_conf: float = 0.0         # live re-scored conviction (latest bar)
    peak_r: float = 0.0            # best unrealized R reached (give-back exit)

    def __post_init__(self):
        if not self.tranches:
            self.tranches = [(self.entry, self.notional)]

    def total_notional(self) -> float:
        return sum(n for _, n in self.tranches)

    def remaining_notional(self) -> float:
        return self.total_notional() * (0.5 if self.tp1_done else 1.0)

    def gross_at(self, px: float, fraction: float = 1.0) -> float:
        """Unrealized gross pnl of `fraction` of the position at price px."""
        d = self.direction
        return sum(d * (px - e) / e * n for e, n in self.tranches) * fraction

    def avg_entry(self) -> float:
        tot = self.total_notional()
        return sum(e * n for e, n in self.tranches) / tot if tot else self.entry

    def open_risk(self, p: Params) -> float:
        loss = -self.gross_at(self.sl, 0.5 if self.tp1_done else 1.0)
        return max(0.0, loss)


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


# --- carry (incremental live state) serialization ---------------------------
# The live loop replays only NEW bars each run and carries the engine's internal
# state forward (open positions, equity, cooldowns, pending fills, circuit/DD).
# These helpers round-trip that carry through JSON so run_live can persist it —
# which is what lets the anchor roll forward instead of re-deriving history.

def _pos_to_dict(pos: Position) -> dict:
    d = asdict(pos)
    d["opened_ts"] = str(pos.opened_ts)
    d["tranches"] = [[float(e), float(n)] for e, n in pos.tranches]
    return d


def _pos_from_dict(d: dict) -> Position:
    d = dict(d)
    d["opened_ts"] = pd.Timestamp(d["opened_ts"])
    d["tranches"] = [tuple(t) for t in d["tranches"]]
    return Position(**d)


def _pending_to_dict(pd_: Pending) -> dict:
    d = asdict(pd_)
    d["decided_ts"] = str(pd_.decided_ts)
    return d


def _pending_from_dict(d: dict) -> Pending:
    d = dict(d)
    d["decided_ts"] = pd.Timestamp(d["decided_ts"])
    return Pending(**d)


def serialize_carry(c: dict) -> dict:
    return {
        "cash": float(c["cash"]), "peak": float(c["peak"]),
        "dd_frozen": bool(c["dd_frozen"]),
        "circuit_until": None if c["circuit_until"] is None else str(c["circuit_until"]),
        "loss_log": [[str(ts), float(x)] for ts, x in c["loss_log"]],
        "cooldown": c["cooldown"],
        "positions": {s: _pos_to_dict(p) for s, p in c["positions"].items()},
        "pendings": [_pending_to_dict(p) for p in c["pendings"]],
    }


def deserialize_carry(d: dict) -> dict:
    return {
        "cash": float(d["cash"]), "peak": float(d["peak"]),
        "dd_frozen": bool(d["dd_frozen"]),
        "circuit_until": None if d.get("circuit_until") is None
        else pd.Timestamp(d["circuit_until"]),
        "loss_log": [(pd.Timestamp(ts), float(x)) for ts, x in d.get("loss_log", [])],
        "cooldown": {k: dict(v) for k, v in d.get("cooldown", {}).items()},
        "positions": {s: _pos_from_dict(v) for s, v in d.get("positions", {}).items()},
        "pendings": [_pending_from_dict(x) for x in d.get("pendings", [])],
    }


def _cost(notional: float, sym: str, p: Params) -> float:
    """Fee component only — slippage is charged inside fill/exit prices."""
    return notional * p.taker_fee * p.cost_mult


def run(frames: dict[str, pd.DataFrame], features: dict[str, pd.DataFrame],
        candidates: dict[str, pd.DataFrame], funding: dict[str, pd.Series],
        regime: pd.DataFrame, corr_alert: pd.Series, p: Params,
        start: pd.Timestamp | None = None, end: pd.Timestamp | None = None,
        equity0: float = 10_000.0, carry: dict | None = None) -> dict:
    grid = pd.DatetimeIndex(sorted(set().union(*[df.index for df in frames.values()])))
    if start is not None:
        grid = grid[grid >= start]
    if end is not None:
        grid = grid[grid <= end]

    bars = {s: df.reindex(grid) for s, df in frames.items()}
    atr1h = {s: features[s]["atr1h"].reindex(grid) for s in frames}
    bias4h = {s: features[s]["bias4h"].reindex(grid) for s in frames}
    # live position-health series (hold_confidence) for the momentum-fade exit;
    # absent in bare unit-feature frames, so read defensively / only when armed
    use_hold = (p.hold_conf_exit > 0 or p.hold_loss_exit > 0) \
        and all("hold_L" in features[s].columns for s in frames)
    hold_at = ({s: {1: features[s]["hold_L"].reindex(grid),
                    -1: features[s]["hold_S"].reindex(grid)} for s in frames}
               if use_hold else None)
    # 4H momentum component alone (loss-side second read): full adverse
    # saturation (mom == 0) also counts toward the LossFade streak, because
    # the regime/alignment 75% of hold_confidence lags a V-reversal and can
    # hold the blended score above the loss floor while the chart has turned
    mom_at = ({s: {1: features[s]["mom_L"].reindex(grid),
                   -1: features[s]["mom_S"].reindex(grid)} for s in frames}
              if use_hold and all("mom_L" in features[s].columns for s in frames)
              else None)
    cand_at = {s: {ts: row for ts, row in candidates[s].iterrows()} for s in candidates}
    fund_at = {s: dict(zip(funding[s].index, funding[s].values))
               for s in funding if funding[s] is not None}
    # Per-asset regime (V3): `regime` may be a dict[sym -> state Series]
    # (per-asset, with BTC-crisis already overridden in), or a single DataFrame
    # (legacy / tests) that is broadcast to every symbol.
    if isinstance(regime, dict):
        state_at = {s: regime[s].reindex(grid).ffill().fillna("RANGE") for s in frames}
    else:
        base_state = regime["state"].reindex(grid).fillna("RANGE")
        state_at = {s: base_state for s in frames}
    corr_at = corr_alert.reindex(grid).fillna(False)

    # fresh, or resumed from a prior run's carry (incremental live replay)
    if carry is None:
        cash, peak = equity0, equity0
        dd_frozen = False
        circuit_until: pd.Timestamp | None = None
        loss_log: list[tuple[pd.Timestamp, float]] = []   # realized losses for 24h circuit
        cooldown: dict[str, int] = {}
        positions: dict[str, Position] = {}
        pendings: list[Pending] = []
    else:
        cash, peak = carry["cash"], carry["peak"]
        dd_frozen = carry["dd_frozen"]
        circuit_until = carry["circuit_until"]
        loss_log = list(carry["loss_log"])
        cooldown = dict(carry["cooldown"])
        positions = dict(carry["positions"])
        pendings = list(carry["pendings"])
    trades: list[dict] = []
    curve: list[tuple[pd.Timestamp, float]] = []

    def mtm(ts_prices: dict[str, float]) -> float:
        u = 0.0
        for s, pos in positions.items():
            px = ts_prices.get(s)
            if px is not None and not np.isnan(px):
                u += pos.gross_at(px, 0.5 if pos.tp1_done else 1.0)
            u += pos.realized
        return cash + u

    def close_part(pos: Position, px: float, fraction: float, ts, reason: str):
        gross = pos.gross_at(px, fraction)
        pos.realized += gross - _cost(pos.total_notional() * fraction, pos.sym, p)

    def finalize(pos: Position, px: float, ts: pd.Timestamp, reason: str):
        nonlocal cash
        close_part(pos, px, 0.5 if pos.tp1_done else 1.0, ts, reason)
        pnl = pos.realized - _cost(pos.notional, pos.sym, p)   # tranche-0 entry cost
        cash += pnl
        if pnl < 0:
            loss_log.append((ts, -pnl))
        r = pnl / pos.risk_amt if pos.risk_amt > 0 else 0.0
        avg_e = pos.avg_entry()
        price_pct = pos.direction * (px - avg_e) / avg_e * 100
        trades.append({
            "sym": pos.sym, "setup": pos.setup, "dir": pos.direction,
            "conf": round(pos.conf, 4), "leverage": pos.leverage,
            "regime": pos.regime, "entry": avg_e, "sl0": pos.sl0,
            "exit": px, "opened": pos.opened_ts, "closed": ts,
            "bars": pos.bars, "r": r, "pnl": pnl, "adds": pos.adds,
            "pnl_pct_price": price_pct, "pnl_pct_lev": price_pct * pos.leverage,
            "reason": reason, "risk_amt": pos.risk_amt,
            "notional": pos.total_notional(),
            "equity_after": cash, **pos.meta,
        })
        scale = p.playbooks.get(pos.setup, {}).get("risk_scale", 1.0)
        cooldown[pos.sym] = {"bars": p.cooldown_bars, "exp": scale < 1.0}
        del positions[pos.sym]

    for i, t in enumerate(grid):
        prices_now = {s: bars[s]["close"].iloc[i] for s in positions}

        # ---- 1. fill pending entries at this bar's open ----
        # whale mode: only one seat is available, so it must go to the best
        # signal across the whole universe (cross-coin selection). Validation
        # status outranks confidence (E15): a PROVEN slot (risk_scale 1.0)
        # always beats an experimental one — regime-lagging markets hand
        # experimental counter-trend slots mechanically high conf (fit/align
        # saturate), and conf does not rank R (E9), so conf alone must never
        # let an unproven signal take the seat from the validated one.
        # Portfolio mode keeps its original fill order.
        fill_order = sorted(
            pendings, reverse=True,
            key=lambda x: (p.playbooks.get(x.setup, {}).get("risk_scale", 1.0),
                           x.conf)) if p.whale_mode else pendings
        for pend in fill_order:
            row = bars[pend.sym].iloc[i]
            if np.isnan(row["open"]):
                continue
            state = state_at[pend.sym].iloc[i]
            if pend.sym in positions:
                # dominance rule: a PROVEN slot (risk_scale 1.0) may displace
                # an experimental position (<1.0) holding the asset — the
                # validated signal always gets the seat. Never the reverse.
                held = positions[pend.sym]
                pend_scale = p.playbooks.get(pend.setup, {}).get("risk_scale", 1.0)
                held_scale = p.playbooks.get(held.setup, {}).get("risk_scale", 1.0)
                if pend_scale >= 1.0 > held_scale:
                    px = row["open"] * (1 - held.direction
                                        * spec(pend.sym).slippage * p.cost_mult)
                    finalize(held, px, t, "Upgrade")
                    cooldown.pop(pend.sym, None)
                else:
                    continue
            eq = mtm(prices_now)
            peak = max(peak, eq)
            dd = 1 - eq / peak if peak > 0 else 0.0
            if dd_frozen and dd <= p.dd_resume:
                dd_frozen = False
            if dd >= p.dd_stop:
                dd_frozen = True
            loss_log[:] = [(ts0, x) for ts0, x in loss_log
                          if (t - ts0) <= pd.Timedelta(hours=24)]  # drop stale entries
            recent_loss = sum(x for _, x in loss_log)
            if recent_loss >= p.circuit_loss_24h * eq:
                circuit_until = t + pd.Timedelta(hours=p.circuit_pause_h)
            # dd_stop freeze: while positions are open, no risk may be added
            # until either recovery to dd_resume or the book goes flat. A FLAT
            # frozen book may re-enter (at the automatic dd_half-halved size):
            # with no open positions equity cannot move, so a full stop would
            # make the documented release ("-15% 회복 시 해제") unreachable — a
            # permanent coma, not the designed pause (E15: whale replays halted
            # forever after the first -20% streak).
            if ((dd_frozen and positions) or state == "CRISIS"
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
            # unproven playbook slots run at reduced size until the forward
            # track earns them full weight (V2 staged-rollout rule)
            scale = p.playbooks.get(pend.setup, {}).get("risk_scale", 1.0)
            risk_frac *= scale
            mult = (0.5 if dd >= p.dd_half else 1.0) * (0.5 if corr_on else 1.0)
            if (dd <= p.eq_boost_dd and not corr_on
                    and pend.sym in p.aggression_syms):
                mult *= p.eq_boost_mult   # anti-martingale: press at equity highs
            # non-whale sizing folds `mult` into risk_frac; whale sizing ignores
            # risk_frac (full margin), so `mult` — AND the experimental
            # risk_scale (E15: it used to vanish here, letting rejected/unproven
            # slots deploy the whole account) — must ride in as margin_frac to
            # keep staged rollout and dd/corr de-risking alive in whale mode too
            sz = size_position(eq, risk_frac * mult, fill, pend.sl, pend.direction, lev,
                               full_margin=p.whale_mode, margin_frac=mult * scale)
            open_risk = sum(pos.open_risk(p) for pos in positions.values())
            open_margin = sum(pos.margin for pos in positions.values())
            if open_risk + sz.risk_amt > p.max_open_risk * eq:
                continue
            if open_margin + sz.margin > p.max_margin_frac * eq:
                continue
            pb = p.playbooks.get(pend.setup, {})
            tp_r = pb.get("tp_r", p.tp1_r)
            tp1 = fill * (1 + pend.direction * tp_r * stop_frac) if tp_r > 0 else 0.0
            positions[pend.sym] = Position(
                sym=pend.sym, direction=pend.direction, conf=pend.conf,
                setup=pend.setup, regime=pend.regime, entry=fill, sl=pend.sl,
                sl0=pend.sl, tp1=tp1, notional=sz.notional, margin=sz.margin,
                risk_amt=sz.risk_amt, leverage=sz.leverage, liq_price=sz.liq_price,
                opened_ts=t, risk_frac=risk_frac * mult, init_stop_frac=stop_frac,
                meta=pend.meta, best_close=fill)
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
            pb = p.playbooks.get(pos.setup, {})
            sl_hit = lo <= pos.sl if d == 1 else hi >= pos.sl
            tp_on = pos.tp1 > 0
            tp_hit = tp_on and (hi >= pos.tp1 if d == 1 else lo <= pos.tp1)
            if sl_hit:  # conservative: stop fills before any TP in the same bar
                px = pos.sl * (1 - d * spec(sym).slippage * p.cost_mult)
                finalize(pos, px, t, "SL" if pos.sl == pos.sl0 else "Trail")
                continue
            if tp_hit and not pos.tp1_done:
                tp_frac = pb.get("tp_frac", p.tp1_frac)
                if tp_frac >= 1.0:   # day-trade playbooks: full exit at target
                    px = pos.tp1 * (1 - d * spec(sym).slippage * p.cost_mult)
                    finalize(pos, px, t, "TP")
                    continue
                close_part(pos, pos.tp1, tp_frac, t, "TP1")
                pos.tp1_done = True
                pos.sl = pos.entry  # break-even stop for the runner
            # close-based management
            pos.best_close = max(pos.best_close, close_px) if d == 1 \
                else min(pos.best_close, close_px)
            a = atr1h[sym].iloc[i]
            trail_w = pb.get("trail_atr", p.trail_atr)
            if trail_w > 0 and not np.isnan(a):  # chandelier, active from entry
                trail = pos.best_close - d * trail_w * a
                if d * (trail - pos.sl) > 0:
                    pos.sl = trail
            # pyramiding (trend playbooks only): add a fixed-risk unit each
            # time the trade proves itself by another +pyramid_trigger_r
            if (p.pyramid_max > 0 and pos.adds < p.pyramid_max
                    and sym in p.aggression_syms and pos.setup.startswith("BRK")
                    and not pos.tp1_done and pos.init_stop_frac > 0):
                k = pos.adds + 1
                trigger = pos.entry * (1 + d * p.pyramid_trigger_r * k
                                       * pos.init_stop_frac)
                dist = d * (close_px - pos.sl) / close_px
                if d * (close_px - trigger) >= 0 and dist > 0:
                    lev_add, _ = final_leverage(pos.conf, dist, pos.regime,
                                                spec(sym).lev_cap, p)
                    if lev_add > 0:
                        eq = mtm({s: bars[s]["close"].iloc[i] for s in positions})
                        fill_add = close_px * (1 + d * spec(sym).slippage * p.cost_mult)
                        notional_add = min(eq * pos.risk_frac / dist, eq * lev_add)
                        add_risk = notional_add * dist
                        open_risk = sum(q.open_risk(p) for q in positions.values())
                        open_margin = sum(q.margin for q in positions.values())
                        if (open_risk + add_risk <= p.max_open_risk * eq
                                and open_margin + notional_add / lev_add
                                <= p.max_margin_frac * eq):
                            pos.realized -= _cost(notional_add, sym, p)
                            pos.tranches.append((fill_add, notional_add))
                            pos.margin += notional_add / lev_add
                            pos.risk_amt += add_risk
                            pos.adds += 1
                            liq_add = fill_add * (1 - d * (1 / lev_add - 0.01))
                            pos.liq_price = max(pos.liq_price, liq_add) if d == 1 \
                                else min(pos.liq_price, liq_add)
            unreal_r = pos.gross_at(close_px) / pos.risk_amt
            pos.peak_r = max(pos.peak_r, unreal_r)
            b4 = bias4h[sym].iloc[i]
            pos.bias_flip_streak = pos.bias_flip_streak + 1 if b4 == -d else 0
            # hourly re-score of this held position (the live conviction the
            # scoring loop reports); a decay below the floor for hold_conf_bars
            # consecutive bars flags a changed situation
            if hold_at is not None:
                h = hold_at[sym][d].iloc[i]
                if not np.isnan(h):
                    pos.hold_conf = float(h)
                    pos.fade_streak = pos.fade_streak + 1 if h < p.hold_conf_exit else 0
                    m = mom_at[sym][d].iloc[i] if mom_at is not None else np.nan
                    mom_broken = not np.isnan(m) and m <= 1e-9
                    pos.loss_fade_streak = (pos.loss_fade_streak + 1
                                            if h < p.hold_loss_exit or mom_broken
                                            else 0)
            exit_px = close_px * (1 - d * spec(sym).slippage * p.cost_mult)
            time_stop = pb.get("time_stop_bars", p.time_stop_bars)
            trend_managed = pb.get("biasflip_exit", True)
            # profit-protecting momentum exit: only ever bank a WINNER that has
            # run to hold_conf_min_r, when either its run stalls (gave back
            # hold_giveback of peak R) or its conviction collapsed
            stalled = (pos.peak_r >= p.hold_conf_min_r
                       and unreal_r <= pos.peak_r * (1 - p.hold_giveback))
            conviction_lost = hold_at is not None and pos.fade_streak >= p.hold_conf_bars
            signal_fade = (p.hold_conf_exit > 0 and trend_managed and unreal_r > 0
                           and (stalled or conviction_lost))
            # capital-protecting early cut: a LOSING trend position whose live
            # conviction has collapsed below the loss floor for hold_conf_bars
            # consecutive bars — regime/momentum turned against the trade — is
            # closed ahead of the structural stop, banking the smaller loss.
            loss_fade = (p.hold_loss_exit > 0 and hold_at is not None
                         and trend_managed and unreal_r < 0
                         and pos.loss_fade_streak >= p.hold_conf_bars)
            if state_at[sym].iloc[i] == "CRISIS":
                finalize(pos, exit_px, t, "Regime")
            elif signal_fade:
                finalize(pos, exit_px, t, "SignalFade")
            elif loss_fade:
                finalize(pos, exit_px, t, "LossFade")
            elif trend_managed and pos.bias_flip_streak >= 2:
                finalize(pos, exit_px, t, "BiasFlip")
            elif (time_stop > 0 and pos.bars >= time_stop
                  and unreal_r < p.time_stop_min_r):
                finalize(pos, exit_px, t, "Timeout")

        # ---- 3. decide at bar close: candidates -> pending fills for t+1h ----
        for sym in cand_at:
            row = cand_at[sym].get(t)
            if row is None:
                continue
            cand_scale = p.playbooks.get(str(row["setup"]), {}).get("risk_scale", 1.0)
            if sym in positions:
                # only a proven candidate over an experimental holder proceeds
                # (the fill step executes the actual Upgrade displacement)
                held_scale = p.playbooks.get(positions[sym].setup, {}) \
                                        .get("risk_scale", 1.0)
                if not (cand_scale >= 1.0 > held_scale):
                    continue
            cd = cooldown.get(sym)
            if cd is not None and not (cand_scale >= 1.0 and cd["exp"]):
                continue
            meta = {k: float(row[k]) for k in
                    ("c_base", "c_fit", "c_align", "c_fund") if k in row.index}
            pendings.append(Pending(sym=sym, direction=int(row["dir"]),
                                    conf=float(row["conf"]), sl=float(row["sl"]),
                                    setup=str(row["setup"]),
                                    regime=state_at[sym].iloc[i],
                                    decided_ts=t, meta=meta))
        for sym in list(cooldown):
            cooldown[sym]["bars"] -= 1
            if cooldown[sym]["bars"] <= 0:
                del cooldown[sym]

        prices_close = {s: bars[s]["close"].iloc[i] for s in positions}
        eq_close = mtm(prices_close)
        peak = max(peak, eq_close)   # continuous peak tracking even on bars
        curve.append((t, eq_close))  # with no pending candidates to evaluate

    eq_curve = pd.Series(dict(curve), name="equity")
    return {"trades": pd.DataFrame(trades), "equity": eq_curve,
            "open_positions": positions, "final_equity": cash,
            "carry": {"cash": cash, "peak": peak, "dd_frozen": dd_frozen,
                      "circuit_until": circuit_until, "loss_log": loss_log,
                      "cooldown": cooldown, "positions": positions,
                      "pendings": pendings}}
