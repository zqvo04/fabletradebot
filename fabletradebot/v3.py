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

    # ---- tempo ----
    bars_per_year: int = BARS_PER_YEAR   # 4H default; 1H variant uses 24*365

    # ---- signal windows (4H bars) ----
    ret_std_win: int = 180            # ~30d realized-vol window
    trend_lookbacks: tuple = (16, 48, 120)   # ~2.7d / 8d / 20d
    trend_scale: float = 2.0          # tanh(z / scale)
    mr_win: int = 20                  # ~3.3d mean-reversion anchor
    mr_scale: float = 2.0
    xs_look: int = 120                # ~20d relative strength
    xs_horizons: tuple | None = None  # multi-horizon blend; None -> (xs_look,)
    xs_min_assets: int = 3            # cross-section needs this many live assets
    xs_residual: bool = False         # rank beta-stripped residual returns
                                      # instead of raw returns (isolates the
                                      # idiosyncratic component in a universe
                                      # of highly correlated names)

    # ---- conviction-tiered leverage (v4 profile; off by default) ----
    conv_enabled: bool = False
    conv_strength_z: float = 0.75     # |xs| that counts as full-strength signal
    disp_win: int = 540               # 90d percentile window for dispersion regime
    # (min conviction, leverage ceiling, vol-budget multiplier) — highest first.
    # Tiers are CEILINGS: realized leverage is what vol targeting asks for,
    # capped by tier x liq_factor. 10x binds only in extreme conviction + low vol.
    conv_tiers: tuple = ((0.80, 10.0, 4.0), (0.60, 5.0, 2.5),
                         (0.35, 3.0, 1.5), (0.00, 2.0, 1.0))
    liq_factor: dict = _d(BTC=1.0, ETH=1.0, SOL=0.7, HYPE=0.4)
    liq_default: float = 0.5
    stress_limit: float = 0.0         # cap: sum |w| x 3sigma-daily <= this (0=off)
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
    lev_default: float = 1.0          # cap for assets not listed above
    beta: dict = _d(BTC=1.0, ETH=1.1, SOL=1.5, HYPE=2.0)
    beta_default: float = 1.3
    gross_cap: float = 2.0            # beta-weighted gross exposure cap

    # ---- rebalancing / costs ----
    band: float = 0.25                # no-trade band, fraction of target
    min_trade: float = 0.005          # min rebalance notional (frac of equity)
    fee_bps: float = 5.0
    slip_bps: dict = _d(BTC=2.0, ETH=2.0, SOL=5.0, HYPE=15.0)
    slip_default: float = 8.0         # conservative default for liquid alts

    # ---- drawdown governor ----
    dd_soft: float = -0.06            # start de-risking here
    dd_hard: float = -0.15            # floor multiplier here (never full stop)
    dd_floor: float = 0.25

    # ---- signal scoring (diagnostic overlay — does NOT affect trading) ----
    # A scored position opens when |weight| crosses score_open_min (or flips
    # sign); TP/SL are placed score_tp_k / score_sl_k daily-sigmas from entry;
    # unresolved by score_timeout_days -> classified by sign of the P&L.
    # These are UNFITTED measurement parameters, not tuned for returns.
    score_open_min: float = 0.05
    score_tp_k: float = 2.0
    score_sl_k: float = 1.5
    score_timeout_days: float = 7.0

    def lev(self, a: str) -> float:
        return self.lev_cap.get(a, self.lev_default)

    def beta_of(self, a: str) -> float:
        return self.beta.get(a, self.beta_default)

    def slip(self, a: str) -> float:
        return self.slip_bps.get(a, self.slip_default)

    def liq(self, a: str) -> float:
        return self.liq_factor.get(a, self.liq_default)


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


def _xs_cross_section(rets: pd.DataFrame, closes: dict, cfg: V3Config,
                      look: int) -> tuple[pd.DataFrame, pd.Series]:
    """Cross-sectional momentum z-panel for one lookback + the raw
    cross-sectional spread (dispersion) before normalization."""
    xs_raw = {}
    if cfg.xs_residual:
        # strip each asset's beta to the equal-weight market so the rank is
        # over idiosyncratic returns, not the common crypto factor
        mkt = rets.mean(axis=1)
        mvar = mkt.rolling(cfg.ret_std_win).var()
        for a in rets:
            beta = rets[a].rolling(cfg.ret_std_win).cov(mkt) / mvar
            resid = rets[a] - beta.shift(1) * mkt   # prior-bar beta: no lookahead
            rsd = resid.rolling(cfg.ret_std_win).std()
            xs_raw[a] = resid.rolling(look).sum() / (rsd * np.sqrt(look))
    else:
        for a, c in closes.items():
            sd = rets[a].rolling(cfg.ret_std_win).std()
            xs_raw[a] = (c / c.shift(look) - 1.0) / (sd * np.sqrt(look))
    xs_panel = pd.DataFrame(xs_raw)
    xs_sd = xs_panel.std(axis=1)
    xs_z = xs_panel.sub(xs_panel.mean(axis=1), axis=0)
    xs_z = xs_z.div(xs_sd.where(xs_sd > 0), axis=0).clip(-2, 2) / 2.0
    # a relative-strength rank over too few names is noise, not signal
    xs_z = xs_z.where(xs_panel.count(axis=1) >= cfg.xs_min_assets)
    return xs_z, xs_sd


def v4_config() -> V3Config:
    """v4 = leveraged risk profile of the frozen v3 signal (see REDESIGN_V4.md).

    What was TESTED AND REJECTED on the 2025 design window before this
    definition (each variant halved Sharpe or worse — full log in the doc):
      - conviction-tiered sizing (|z| extremity x agreement x dispersion),
      - multi-horizon signal blends (60/120/180),
      - dispersion-regime budget tiers.
    Measured conclusion: the XS edge is LINEAR in the signal — every attempt
    to bet more on "stronger" signals added variance faster than return.

    Also rejected: raising the per-asset weight ceilings (BTC/ETH 1.5x,
    SOL 1.0x, HYPE 0.5x -> 5/5/3/2). Full-period Sharpe fell 2.00 -> 1.28:
    the tight ceilings are load-bearing — they stop vol targeting from
    piling into the riskiest names whenever their trailing vol dips.

    v4 is therefore the AGGRESSIVE RISK PROFILE of the unchanged v3 signal:
    vol budget 0.40 (the measured frontier point: ~2x return, Sharpe decay
    2.16 -> ~2.0), original ceilings, plus a liquidation guard — a 3-sigma
    correlated daily shock may cost at most 25% of equity. Realized gross
    leverage stays ~0.6-1.7x; the exchange 2x/3x/5x/10x tiers exist only as
    account-level hard stops above these software ceilings.
    """
    cfg = v3_config()
    cfg.vol_budget = 0.40
    cfg.stress_limit = 0.25
    # governor bands are vol-denominated: scale with the profile (2x budget
    # -> 2x bands), otherwise ordinary noise at the higher vol permanently
    # throttles the book (measured: design-window sign flip at fixed bands)
    cfg.dd_soft = -0.12
    cfg.dd_hard = -0.30
    # trade the cross-section only when the FULL 4-name universe is live:
    # the 3-name book (pre-HYPE) measured -1.96% standalone (ASSESSMENT #7)
    cfg.xs_min_assets = 4
    return cfg


def sleeve_signals(data: dict, cfg: V3Config) -> dict:
    """Per-asset DataFrame of sleeve signals in [-1,1] + realized vol +
    conviction inputs (horizon agreement, dispersion-regime percentile).
    All columns are computable from information up to and including bar i."""
    from .indicators import pct_rank

    out = {}
    closes = {a: df["close"] for a, df in data.items()}
    # cross-sectional momentum needs the panel: risk-adjusted lookback return
    rets = pd.DataFrame({a: np.log(c / c.shift(1)) for a, c in closes.items()})
    horizons = cfg.xs_horizons or (cfg.xs_look,)
    zs, sds = [], []
    for lb in horizons:
        z, sd_x = _xs_cross_section(rets, closes, cfg, lb)
        zs.append(z)
        sds.append(sd_x)
    xs_z = sum(zs) / len(zs)
    if len(zs) > 1:  # horizon agreement in [0,1]
        agree = sum((np.sign(z) == np.sign(xs_z)).astype(float) for z in zs) / len(zs)
        agree = agree.where(xs_z.notna())
    else:
        agree = xs_z.notna().astype(float).where(xs_z.notna())
    disp = sum(sds) / len(sds)                       # cross-sectional spread
    disp_pct = pct_rank(disp, cfg.disp_win)          # 0-100, trailing window

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
        s["agree"] = agree[a].reindex(df.index)
        s["disp_pct"] = disp_pct.reindex(df.index)
        s["vol_ann"] = sd * np.sqrt(cfg.bars_per_year)
        out[a] = s
    return out


def conviction(s: dict, cfg: V3Config) -> float:
    """Conviction in [0,1] = signal strength x horizon agreement x
    dispersion-regime quality. Missing components degrade to neutral 0.5."""
    z = abs(s["xs"])
    strength = min(z / cfg.conv_strength_z, 1.0)
    agree = s.get("agree", np.nan)
    disp = s.get("disp_pct", np.nan)
    a_f = 0.5 + 0.5 * (agree if not np.isnan(agree) else 0.5)
    d_f = 0.5 + 0.5 * (disp / 100.0 if not np.isnan(disp) else 0.5)
    return float(strength * a_f * d_f)


def _tier(conv: float, cfg: V3Config) -> tuple[float, float]:
    """(leverage ceiling, vol-budget multiplier) for a conviction level."""
    for min_conv, lev, mult in cfg.conv_tiers:
        if conv >= min_conv:
            return lev, mult
    return cfg.conv_tiers[-1][1], cfg.conv_tiers[-1][2]


def target_weights(sig_row: dict, equity_dd: float, cfg: V3Config) -> dict:
    """Ensemble -> vol-target (conviction-scaled when enabled) -> caps ->
    stress cap -> drawdown governor. Returns {asset: signed weight}."""
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
        budget, lev = cfg.vol_budget, cfg.lev(a)
        if cfg.conv_enabled:
            tier_lev, tier_mult = _tier(conviction(s, cfg), cfg)
            budget = cfg.vol_budget * tier_mult
            lev = tier_lev * cfg.liq(a)   # tier ceiling scaled by liquidity class
        raw = sig * budget / s["vol_ann"]
        w[a] = float(np.clip(raw, -lev, lev))
    gross_beta = sum(abs(v) * cfg.beta_of(a) for a, v in w.items())
    if gross_beta > cfg.gross_cap:
        scale = cfg.gross_cap / gross_beta
        w = {a: v * scale for a, v in w.items()}
    # stress cap: a correlated 3-sigma daily shock (no diversification credit)
    # must not cost more than stress_limit of equity — the liquidation guard
    if cfg.stress_limit > 0:
        stress = sum(
            abs(v) * sig_row[a]["vol_ann"] / np.sqrt(365.0) * 3.0
            for a, v in w.items()
            if v != 0.0 and not np.isnan(sig_row[a]["vol_ann"]))
        if stress > cfg.stress_limit:
            w = {a: v * cfg.stress_limit / stress for a, v in w.items()}
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
        # UNION index: assets listing mid-period join when their own history
        # is warm (NaN rows gate them out) instead of truncating the panel.
        index = None
        for df in self.data.values():
            index = df.index if index is None else index.union(df.index)
        index = index.sort_values()
        data = {a: df.reindex(index) for a, df in self.data.items()}
        sigs = sleeve_signals(data, cfg)
        arr = {a: {
            "open": data[a]["open"].to_numpy(float),
            "close": data[a]["close"].to_numpy(float),
            "mark": data[a]["close"].ffill().to_numpy(float),  # marking only
            "tsm": sigs[a]["tsm"].to_numpy(float),
            "mr": sigs[a]["mr"].to_numpy(float),
            "xs": sigs[a]["xs"].to_numpy(float),
            "agree": sigs[a]["agree"].to_numpy(float),
            "disp_pct": sigs[a]["disp_pct"].to_numpy(float),
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
                eq_open = cash + sum(
                    qty[a] * arr[a]["mark"][i] for a in assets if qty[a] != 0.0)
                for a in assets:
                    px = arr[a]["open"][i]
                    if np.isnan(px):              # asset not trading this bar
                        continue
                    tgt_qty = pending[a] * eq_open / px
                    delta = tgt_qty - qty[a]
                    notional = abs(delta) * px
                    if notional < cfg.min_trade * eq_open:
                        continue
                    if abs(pending[a]) > 0 and \
                            abs(delta * px) < cfg.band * abs(pending[a]) * eq_open:
                        continue
                    side = 1.0 if delta > 0 else -1.0
                    fill = px * (1 + side * cfg.slip(a) * 1e-4)
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
                        cost = qty[a] * arr[a]["mark"][i] * rate
                        cash -= cost
                        funding_paid += cost

            # 3) mark equity at close, update governor state
            equity = cash + sum(
                qty[a] * arr[a]["mark"][i] for a in assets if qty[a] != 0.0)
            equity_curve[i] = equity
            hwm = max(hwm, equity)
            dd = equity / hwm - 1.0

            # 4) decide next bar's target weights from this close
            if i >= cfg.min_history and equity > 0:
                row = {a: {k: arr[a][k][i] for k in
                           ("tsm", "mr", "xs", "agree", "disp_pct", "vol_ann")}
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
        bpy = self.cfg.bars_per_year
        r = equity.pct_change().dropna()
        years = len(equity) / bpy
        monthly_eq = equity.resample("ME").last()
        prev = pd.concat([pd.Series([self.equity0]), monthly_eq.iloc[:-1]])
        monthly = pd.Series(monthly_eq.values / prev.values - 1.0, index=monthly_eq.index)
        ann_vol = float(r.std() * np.sqrt(bpy))
        mean_ann = float(r.mean() * bpy)
        return dict(
            bars=len(equity), assets=assets,
            total_return=float(equity.iloc[-1] / self.equity0 - 1.0),
            max_dd=float((equity / equity.cummax() - 1.0).min()),
            ann_vol=ann_vol, sharpe=mean_ann / ann_vol if ann_vol > 0 else 0.0,
            turnover_yr=turnover / years if years > 0 else 0.0,
            fees=fees, funding=funding_paid, n_rebalances=n_rebal,
            monthly=monthly,
        )
