"""Signal scoring overlay — turns each fired signal into a trackable trade
with TP/SL/timeout, then grades it Win / Loss / Timeout-Win / Timeout-Loss.

This is a DIAGNOSTIC layer over the continuous portfolio: it never changes
a target weight. It answers "if you had entered on this signal with a fixed
TP/SL, would it have worked out?" — a pure signal-quality question that lets
the Notion log show open positions and their eventual outcomes.

A scored position opens for an asset when its target weight crosses
score_open_min in magnitude (or flips sign) and none is already open for it.
TP/SL sit score_tp_k / score_sl_k daily-sigmas from entry. Resolution walks
subsequent bars: intrabar TP or SL touch (SL assumed first if both, the
conservative choice); at the timeout horizon, the sign of close-vs-entry
decides Timeout-Win vs Timeout-Loss. R is signed P&L over the SL distance.
"""
import numpy as np
import pandas as pd

WIN, LOSS = "Win", "Loss"
TIMEOUT_WIN, TIMEOUT_LOSS = "Timeout-Win", "Timeout-Loss"
OPEN = "Open"


def open_position(system: str, asset: str, direction: int, entry: float,
                  sigma_day: float, ts, cfg, weight: float, equity: float) -> dict:
    tp_dist = cfg.score_tp_k * sigma_day * entry
    sl_dist = cfg.score_sl_k * sigma_day * entry
    opened = pd.Timestamp(ts)
    return dict(
        id=f"{system}:{asset}:{opened.isoformat()}",
        system=system, asset=asset, direction=direction,
        entry=float(entry),
        tp=float(entry + direction * tp_dist),
        sl=float(entry - direction * sl_dist),
        risk=float(sl_dist),
        opened_ts=opened.isoformat(),
        timeout_ts=(opened + pd.Timedelta(days=cfg.score_timeout_days)).isoformat(),
        weight=float(weight), equity=float(equity),
        status=OPEN, exit=None, result_r=None, closed_ts=None,
    )


def step_position(pos: dict, high: float, low: float, close: float, ts) -> bool:
    """Advance one bar. Mutates pos to a terminal status and returns True when
    it resolves; returns False while it stays open."""
    d, tp, sl = pos["direction"], pos["tp"], pos["sl"]
    hit_tp = high >= tp if d > 0 else low <= tp
    hit_sl = low <= sl if d > 0 else high >= sl
    if hit_sl:                       # conservative: SL wins a same-bar tie
        return _close(pos, LOSS, sl, ts)
    if hit_tp:
        return _close(pos, WIN, tp, ts)
    if pd.Timestamp(ts) >= pd.Timestamp(pos["timeout_ts"]):
        in_profit = d * (close - pos["entry"]) > 0
        return _close(pos, TIMEOUT_WIN if in_profit else TIMEOUT_LOSS, close, ts)
    return False


def _close(pos: dict, status: str, exit_px: float, ts) -> bool:
    pos["status"] = status
    pos["exit"] = float(exit_px)
    pos["result_r"] = float(pos["direction"] * (exit_px - pos["entry"]) / pos["risk"]) \
        if pos["risk"] > 0 else 0.0
    pos["closed_ts"] = pd.Timestamp(ts).isoformat()
    return True


def simulate_scoring(weights: pd.DataFrame, data: dict, sigs: dict,
                     equity: pd.Series, cfg, system: str) -> list[dict]:
    """Replay the whole weight history and return every scored position
    (resolved + still-open). Deterministic — identical inputs, identical ids."""
    assets = list(weights.columns)
    idx = weights.index
    W = {a: weights[a].to_numpy(float) for a in assets}
    H = {a: data[a]["high"].reindex(idx).to_numpy(float) for a in assets}
    L = {a: data[a]["low"].reindex(idx).to_numpy(float) for a in assets}
    C = {a: data[a]["close"].reindex(idx).to_numpy(float) for a in assets}
    V = {a: sigs[a]["vol_ann"].reindex(idx).to_numpy(float) for a in assets}
    EQ = equity.reindex(idx).to_numpy(float)

    open_pos: dict[str, dict] = {}
    done: list[dict] = []
    for i in range(len(idx)):
        ts = idx[i]
        for a in list(open_pos):     # resolve first, against this bar
            if step_position(open_pos[a], H[a][i], L[a][i], C[a][i], ts):
                done.append(open_pos.pop(a))
        for a in assets:             # then open fresh crossings
            w = W[a][i]
            if np.isnan(w) or a in open_pos:
                continue
            wp = 0.0 if i == 0 or np.isnan(W[a][i - 1]) else W[a][i - 1]
            d = 1 if w > 1e-12 else -1 if w < -1e-12 else 0
            dp = 1 if wp > 1e-12 else -1 if wp < -1e-12 else 0
            crossed = abs(w) >= cfg.score_open_min and (abs(wp) < cfg.score_open_min or d != dp)
            if d == 0 or not crossed:
                continue
            sd = V[a][i] / np.sqrt(365.0)
            if np.isnan(sd) or sd <= 0 or np.isnan(C[a][i]):
                continue
            open_pos[a] = open_position(system, a, d, C[a][i], sd, ts, cfg,
                                        weight=w, equity=EQ[i])
    return done + list(open_pos.values())


def summarize(positions: list[dict]) -> dict:
    """Win-rate / expectancy breakdown over resolved positions."""
    resolved = [p for p in positions if p["status"] != OPEN]
    n = len(resolved)
    if not n:
        return dict(n=0, open=len(positions))
    wins = sum(1 for p in resolved if p["status"] in (WIN, TIMEOUT_WIN))
    rs = [p["result_r"] for p in resolved]
    counts = {s: sum(1 for p in resolved if p["status"] == s)
              for s in (WIN, LOSS, TIMEOUT_WIN, TIMEOUT_LOSS)}
    return dict(n=n, open=sum(1 for p in positions if p["status"] == OPEN),
                win_rate=wins / n, avg_r=float(np.mean(rs)),
                sum_r=float(np.sum(rs)), counts=counts)
