"""v3 — end-to-end continuous portfolio system (architecture replacement).

v1/v2 pick discrete trades (entry/stop/exit) from playbooks gated by a regime
classifier. Their measured limits: one strategy class (breakout trend), ~2.7
trades/month, all-or-nothing exposure, and an over-Kelly cliff from discrete
per-trade risk. v3 replaces the architecture:

  sleeve signals in [-1,1]  ->  ensemble  ->  vol-targeted weights
  ->  cost-aware rebalancing (no-trade band)  ->  drawdown governor

Sleeves (each a different return source):
  TSM  time-series momentum, 3 lookbacks (the proven v1/v2 edge, made continuous)
  MR   short-horizon mean reversion (earns in the chop where TSM bleeds)
  XS   cross-sectional momentum across the 4 assets (relative strength,
       partially market-neutral)

Positions are fractions of equity, resized every 4H bar only when the target
leaves the no-trade band — frequency without churn. Signals use bar-i close;
fills happen at bar i+1 open with taker fee + slippage. Funding is paid on
open positions at 8h events. No lookahead anywhere.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

BARS_PER_YEAR = 6 * 365  # 4H bars


def _d(**kw):
    return field(default_factory=lambda: dict(kw))


@dataclass
class V3Config:
    assets: tuple = ("BTC", "ETH", "SOL", "HYPE")

    # ---- signal windows (4H bars) ----
    ret_std_win: int = 180            # ~30d realized-vol window
    trend_lookbacks: tuple = (16, 48, 120)   # ~2.7d / 8d / 20d
    trend_scale: float = 2.0          # tanh(z / scale)
    mr_win: int = 20                  # ~3.3d mean-reversion anchor
    mr_scale: float = 2.0
    xs_look: int = 120                # ~20d relative strength
    min_history: int = 200            # bars before an asset may trade

    # ---- ensemble (design-window choice) ----
    w_trend: float = 1.0
    w_mr: float = 0.0
    w_xs: float = 0.0
    deadband: float = 0.0             # |ensemble| below this maps to 0 exposure
                                      # (continuous analogue of v1/v2's theta gate)

    # ---- sizing ----
    vol_budget: float = 0.20          # annualized vol per asset at |signal| = 1
    lev_cap: dict = _d(BTC=1.5, ETH=1.5, SOL=1.0, HYPE=0.5)   # |weight| cap
    beta: dict = _d(BTC=1.0, ETH=1.1, SOL=1.5, HYPE=2.0)
    gross_cap: float = 2.0            # beta-weighted gross exposure cap

    # ---- rebalancing / costs ----
    band: float = 0.25                # no-trade band, fraction of target
    min_trade: float = 0.005          # min rebalance notional (frac of equity)
    fee_bps: float = 5.0
    slip_bps: dict = _d(BTC=2.0, ETH=2.0, SOL=5.0, HYPE=15.0)

    # ---- drawdown governor ----
    dd_soft: float = -0.06            # start de-risking here
    dd_hard: float = -0.15            # floor multiplier here (never full stop)
    dd_floor: float = 0.25


def v3_config() -> V3Config:
    """Frozen v3 (designed on 2025 only; see REDESIGN_V3.md).

    Sleeve evaluation on the design window: continuous TSM and MR are net
    losers after costs (turnover 80-90x/yr); cross-sectional momentum is the
    one sleeve with a real, structure-checked edge — near-zero net exposure,
    balanced long/short across assets, Sharpe positive for every lookback in
    60..180. Blending TSM/MR back in only diluted it, so v3 trades XS alone
    with vol targeting, a wide no-trade band, and the drawdown governor.
    """
    return V3Config(
        w_trend=0.0, w_mr=0.0, w_xs=1.0,
        band=0.5, min_trade=0.02,
    )


def sleeve_signals(data: dict, cfg: V3Config) -> dict:
    """Per-asset DataFrame of sleeve signals in [-1,1] + realized vol.
    All columns are computable from information up to and including bar i."""
    out = {}
    closes = {a: df["close"] for a, df in data.items()}
    # cross-sectional momentum needs the panel: risk-adjusted lookback return
    xs_raw = {}
    for a, c in closes.items():
        r = np.log(c / c.shift(1))
        sd = r.rolling(cfg.ret_std_win).std()
        xs_raw[a] = (c / c.shift(cfg.xs_look) - 1.0) / (sd * np.sqrt(cfg.xs_look))
    xs_panel = pd.DataFrame(xs_raw)
    xs_z = xs_panel.sub(xs_panel.mean(axis=1), axis=0)
    xs_sd = xs_panel.std(axis=1)
    xs_z = xs_z.div(xs_sd.where(xs_sd > 0), axis=0).clip(-2, 2) / 2.0

    for a, df in data.items():
        c = df["close"]
        r = np.log(c / c.shift(1))
        sd = r.rolling(cfg.ret_std_win).std()
        s = pd.DataFrame(index=df.index)
        # TSM: z-scored lookback returns -> tanh, averaged over lookbacks
        acc = 0.0
        for lb in cfg.trend_lookbacks:
            z = (c / c.shift(lb) - 1.0) / (sd * np.sqrt(lb))
            acc = acc + np.tanh(z / cfg.trend_scale)
        s["tsm"] = acc / len(cfg.trend_lookbacks)
        # MR: fade the short-horizon excursion from its moving anchor
        zmr = (c - c.rolling(cfg.mr_win).mean()) / (sd * np.sqrt(cfg.mr_win) * c)
        s["mr"] = -np.tanh(zmr / cfg.mr_scale)
        s["xs"] = xs_z[a].reindex(df.index)
        s["vol_ann"] = sd * np.sqrt(BARS_PER_YEAR)
        out[a] = s
    return out


def target_weights(sig_row: dict, equity_dd: float, cfg: V3Config) -> dict:
    """Ensemble -> vol-target -> caps -> drawdown governor. Returns
    {asset: signed weight as fraction of equity} for one bar."""
    w = {}
    for a, s in sig_row.items():
        if any(np.isnan(v) for v in (s["tsm"], s["mr"], s["xs"], s["vol_ann"])) \
                or s["vol_ann"] <= 0:
            w[a] = 0.0
            continue
        sig = cfg.w_trend * s["tsm"] + cfg.w_mr * s["mr"] + cfg.w_xs * s["xs"]
        sig = float(np.clip(sig, -1.0, 1.0))
        if cfg.deadband > 0:
            if abs(sig) <= cfg.deadband:
                sig = 0.0
            else:  # rescale the live range back to (0, 1]
                sig = np.sign(sig) * (abs(sig) - cfg.deadband) / (1.0 - cfg.deadband)
        raw = sig * cfg.vol_budget / s["vol_ann"]
        w[a] = float(np.clip(raw, -cfg.lev_cap[a], cfg.lev_cap[a]))
    gross_beta = sum(abs(v) * cfg.beta[a] for a, v in w.items())
    if gross_beta > cfg.gross_cap:
        scale = cfg.gross_cap / gross_beta
        w = {a: v * scale for a, v in w.items()}
    # drawdown governor: linear de-risk between dd_soft and dd_hard
    if equity_dd <= cfg.dd_soft:
        span = cfg.dd_hard - cfg.dd_soft
        frac = (equity_dd - cfg.dd_soft) / span if span < 0 else 1.0
        mult = max(cfg.dd_floor, 1.0 + (cfg.dd_floor - 1.0) * min(frac, 1.0))
        w = {a: v * mult for a, v in w.items()}
    return w


@dataclass
class V3Results:
    equity: pd.Series
    weights: pd.DataFrame
    stats: dict

    def summary(self) -> str:
        s = self.stats
        lines = [
            f"bars: {s['bars']}  assets: {s['assets']}",
            f"total return: {s['total_return']:+.2%}   max drawdown: {s['max_dd']:.2%}",
            f"ann vol: {s['ann_vol']:.1%}   sharpe: {s['sharpe']:.2f}   "
            f"turnover/yr: {s['turnover_yr']:.1f}x",
            f"fees paid: {s['fees']:.2f}   net funding paid: {s['funding']:.2f}   "
            f"rebalances: {s['n_rebalances']}",
            "monthly returns:",
        ]
        for ts, r in s["monthly"].items():
            lines.append(f"  {ts.strftime('%Y-%m')}: {r:+.2%}")
        return "\n".join(lines)


class V3Backtester:
    """Continuous-weight portfolio simulator on aligned 4H bars."""

    def __init__(self, data: dict, cfg: V3Config | None = None,
                 funding: dict | None = None, equity0: float = 100_000.0):
        self.cfg = cfg or V3Config()
        self.data = {a: df for a, df in data.items() if a in self.cfg.assets}
        self.funding = funding or {}
        self.equity0 = equity0

    def run(self) -> V3Results:
        cfg = self.cfg
        index = None
        for df in self.data.values():
            index = df.index if index is None else index.intersection(df.index)
        index = index.sort_values()
        data = {a: df.loc[index] for a, df in self.data.items()}
        sigs = sleeve_signals(data, cfg)
        arr = {a: {
            "open": data[a]["open"].to_numpy(float),
            "close": data[a]["close"].to_numpy(float),
            "tsm": sigs[a]["tsm"].to_numpy(float),
            "mr": sigs[a]["mr"].to_numpy(float),
            "xs": sigs[a]["xs"].to_numpy(float),
            "vol_ann": sigs[a]["vol_ann"].to_numpy(float),
            "funding": self._funding_arr(index, a),
        } for a in data}
        assets = list(data)
        n = len(index)

        cash = self.equity0
        qty = {a: 0.0 for a in assets}          # signed base units
        fees = funding_paid = turnover = 0.0
        n_rebal = 0
        hwm = self.equity0
        pending = None                          # weights decided at bar i-1 close
        equity_curve = np.empty(n)
        w_hist = np.zeros((n, len(assets)))

        for i in range(n):
            # 1) execute last bar's targets at this bar's open
            if pending is not None:
                eq_open = cash + sum(qty[a] * arr[a]["open"][i] for a in assets)
                for a in assets:
                    px = arr[a]["open"][i]
                    tgt_qty = pending[a] * eq_open / px
                    delta = tgt_qty - qty[a]
                    notional = abs(delta) * px
                    if notional < cfg.min_trade * eq_open:
                        continue
                    if abs(pending[a]) > 0 and \
                            abs(delta * px) < cfg.band * abs(pending[a]) * eq_open:
                        continue
                    side = 1.0 if delta > 0 else -1.0
                    fill = px * (1 + side * cfg.slip_bps[a] * 1e-4)
                    fee = notional * cfg.fee_bps * 1e-4
                    cash -= delta * fill + fee
                    fees += fee
                    turnover += notional / eq_open
                    qty[a] += delta
                    n_rebal += 1
                pending = None

            # 2) funding events (8h) on open positions
            ts = index[i]
            if ts.hour % 8 == 0 and ts.minute == 0:
                for a in assets:
                    rate = arr[a]["funding"][i]
                    if not np.isnan(rate) and qty[a] != 0.0:
                        cost = qty[a] * arr[a]["close"][i] * rate
                        cash -= cost
                        funding_paid += cost

            # 3) mark equity at close, update governor state
            equity = cash + sum(qty[a] * arr[a]["close"][i] for a in assets)
            equity_curve[i] = equity
            hwm = max(hwm, equity)
            dd = equity / hwm - 1.0

            # 4) decide next bar's target weights from this close
            if i >= cfg.min_history and equity > 0:
                row = {a: {k: arr[a][k][i] for k in ("tsm", "mr", "xs", "vol_ann")}
                       for a in assets}
                pending = target_weights(row, dd, cfg)
                for j, a in enumerate(assets):
                    w_hist[i, j] = pending[a]

        eq = pd.Series(equity_curve, index=index, name="equity")
        weights = pd.DataFrame(w_hist, index=index, columns=assets)
        return V3Results(eq, weights, self._stats(eq, fees, funding_paid,
                                                  turnover, n_rebal, assets))

    def _funding_arr(self, index, asset):
        f = self.funding.get(asset)
        if f is None or getattr(f, "empty", True):
            return np.full(len(index), np.nan)
        f = f.dropna().sort_index()
        f = f[~f.index.duplicated(keep="last")]
        return f.reindex(index).to_numpy(float)

    def _stats(self, equity, fees, funding_paid, turnover, n_rebal, assets):
        r = equity.pct_change().dropna()
        years = len(equity) / BARS_PER_YEAR
        monthly_eq = equity.resample("ME").last()
        prev = pd.concat([pd.Series([self.equity0]), monthly_eq.iloc[:-1]])
        monthly = pd.Series(monthly_eq.values / prev.values - 1.0, index=monthly_eq.index)
        ann_vol = float(r.std() * np.sqrt(BARS_PER_YEAR))
        mean_ann = float(r.mean() * BARS_PER_YEAR)
        return dict(
            bars=len(equity), assets=assets,
            total_return=float(equity.iloc[-1] / self.equity0 - 1.0),
            max_dd=float((equity / equity.cummax() - 1.0).min()),
            ann_vol=ann_vol, sharpe=mean_ann / ann_vol if ann_vol > 0 else 0.0,
            turnover_yr=turnover / years if years > 0 else 0.0,
            fees=fees, funding=funding_paid, n_rebalances=n_rebal,
            monthly=monthly,
        )
