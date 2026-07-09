"""Event-driven backtester over aligned 1H bars, following the blueprint's
main-loop order: breakers -> manage open positions -> new entries."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config
from .engine import Broker, Engine
from .preprocess import clean_ohlcv
from .regime import build_features, CRISIS
from .risk import RiskManager, OK

FEATURE_COLS = [
    "open", "high", "low", "close", "volume", "atr", "ema20", "ema100", "er",
    "tstat", "sigma", "v_pct", "bbw", "bbw_pct", "don_hi", "don_lo", "don_hi_f",
    "don_lo_f", "swing_hi", "swing_lo", "vol_sma", "e2raw", "ret4h", "funding", "f_z",
]


@dataclass
class Results:
    equity: pd.Series
    trades: pd.DataFrame
    stats: dict

    def summary(self) -> str:
        s = self.stats
        lines = [
            f"bars: {s['bars']}  assets: {s['assets']}",
            f"total return: {s['total_return']:+.2%}   max drawdown: {s['max_dd']:.2%}",
            f"trades: {s['n_trades']}  win rate: {s['win_rate']:.1%}  avg R: {s['avg_r']:+.2f}"
            f"  profit factor: {s['profit_factor']:.2f}",
            f"fees paid: {s['fees']:.2f}   net funding paid: {s['funding']:.2f}",
            "monthly returns:",
        ]
        for ts, r in s["monthly"].items():
            lines.append(f"  {ts.strftime('%Y-%m')}: {r:+.2%}")
        lines.append("by playbook (n / win% / avg R / total R):")
        for pb, row in s["by_playbook"].items():
            lines.append(
                f"  {pb}: {row['n']:>4} / {row['win']:.0%} / {row['avg_r']:+.2f} / {row['sum_r']:+.1f}")
        return "\n".join(lines)


class Backtester:
    def __init__(self, data: dict, cfg: Config | None = None,
                 funding: dict | None = None, equity0: float = 100_000.0):
        """data: {asset: OHLCV DataFrame (1H, DatetimeIndex)};
        funding: optional {asset: 8h funding-rate Series}."""
        self.cfg = cfg or Config()
        self.data = data
        self.funding = funding or {}
        self.equity0 = equity0

    def run(self) -> Results:
        cfg = self.cfg
        feats = {}
        for asset, df in self.data.items():
            cleaned = clean_ohlcv(df)
            if len(cleaned) < cfg.min_history_bars:
                continue  # blueprint: insufficient history -> excluded
            feats[asset] = build_features(cleaned, self.funding.get(asset), cfg)
        if not feats:
            raise ValueError("no asset has enough clean history")

        index = None
        for f in feats.values():
            index = f.index if index is None else index.intersection(f.index)
        index = index.sort_values()
        if len(index) == 0:
            raise ValueError("assets share no common timestamps")

        arrays = {}
        for asset, f in feats.items():
            f = f.loc[index]
            arrays[asset] = {c: f[c].to_numpy(dtype=float) for c in FEATURE_COLS}
            arrays[asset]["regime"] = np.array(f["regime"].tolist(), dtype=object)

        broker = Broker(cfg, self.equity0)
        risk = RiskManager(cfg)
        engine = Engine(cfg, broker, risk)
        for asset in arrays:
            engine.positions[asset] = None

        equity_curve = np.empty(len(index))
        has_btc = "BTC" in arrays

        for i, ts in enumerate(index):
            marks = {a: arrays[a]["close"][i] for a in arrays}

            if ts.hour % 8 == 0 and ts.minute == 0:  # funding events
                for a, pos in engine.positions.items():
                    if pos is not None:
                        rate = arrays[a]["funding"][i]
                        if not np.isnan(rate):
                            broker.pay_funding(pos.direction, pos.qty, marks[a], rate)

            equity = broker.equity(engine.positions, marks)
            status = risk.on_bar(ts, equity)
            if status != OK:
                engine.flatten_all(marks, i, ts, status)
                equity_curve[i] = broker.equity(engine.positions, marks)
                continue

            btc_regime = arrays["BTC"]["regime"][i] if has_btc else CRISIS
            for a in arrays:
                engine.manage(a, arrays[a], i, ts, btc_regime)

            equity = broker.equity(engine.positions, marks)
            for a in arrays:
                if a in cfg.majors:
                    btc_ctx = None
                elif has_btc:
                    btc_ctx = {"regime": btc_regime, "ret4h": arrays["BTC"]["ret4h"][i]}
                else:
                    continue  # no BTC context -> alts stay gated off
                engine.try_enter(a, arrays[a], i, ts, btc_ctx, equity, marks)

            equity_curve[i] = broker.equity(engine.positions, marks)

        self._engine, self._broker = engine, broker  # exposed for tests
        equity_s = pd.Series(equity_curve, index=index, name="equity")
        trades = pd.DataFrame(engine.trades)
        return Results(equity_s, trades, self._stats(equity_s, trades, broker, arrays))

    def _stats(self, equity: pd.Series, trades: pd.DataFrame, broker: Broker,
               arrays: dict) -> dict:
        monthly_eq = equity.resample("ME").last()
        prev = pd.concat([pd.Series([self.equity0]), monthly_eq.iloc[:-1]])
        monthly = pd.Series(monthly_eq.values / prev.values - 1.0, index=monthly_eq.index)
        dd = equity / equity.cummax() - 1.0
        n = len(trades)
        wins = trades[trades["pnl"] > 0] if n else trades
        losses = trades[trades["pnl"] <= 0] if n else trades
        gross_w = wins["pnl"].sum() if n else 0.0
        gross_l = -losses["pnl"].sum() if n else 0.0
        by_pb = {}
        if n:
            for pb, g in trades.groupby("playbook"):
                by_pb[pb] = dict(n=len(g), win=(g["pnl"] > 0).mean(),
                                 avg_r=g["r"].mean(), sum_r=g["r"].sum())
        return dict(
            bars=len(equity), assets=list(arrays),
            total_return=equity.iloc[-1] / self.equity0 - 1.0,
            max_dd=dd.min(), monthly=monthly, n_trades=n,
            win_rate=(trades["pnl"] > 0).mean() if n else 0.0,
            avg_r=trades["r"].mean() if n else 0.0,
            profit_factor=gross_w / gross_l if gross_l > 0 else float("inf"),
            fees=broker.fees, funding=broker.funding, by_playbook=by_pb,
        )
