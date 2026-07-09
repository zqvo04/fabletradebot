"""End-to-end integration on synthetic regime-switching data."""
import numpy as np

from fabletradebot.backtest import Backtester
from fabletradebot.config import test_config as make_cfg
from fabletradebot.synthetic import generate_market


def test_end_to_end_runs_and_accounting_is_consistent():
    data, funding = generate_market(n_bars=2600, seed=11)
    bt = Backtester(data, make_cfg(), funding=funding, equity0=100_000.0)
    res = bt.run()

    assert np.isfinite(res.equity).all()
    assert (res.equity > 0).all()
    assert res.stats["n_trades"] >= 5  # the system actually trades

    # accounting identity: equity_end = equity0 + closed pnl + open unrealized - funding
    engine, broker = bt._engine, bt._broker
    closed = res.trades["pnl"].sum() if len(res.trades) else 0.0
    last_marks = {a: data[a]["close"].iloc[-1] for a in data}
    open_unrl = sum(
        p.cash_flow + p.direction * p.qty * last_marks[a]
        for a, p in engine.positions.items() if p is not None)
    expected = 100_000.0 + closed + open_unrl - broker.funding
    assert abs(res.equity.iloc[-1] - expected) < 1e-6 * 100_000.0

    # circuit breakers keep the close-marked curve well away from ruin
    assert res.stats["max_dd"] > -0.35


def test_missing_data_rows_are_dropped_not_traded():
    data, funding = generate_market(n_bars=2600, seed=12)
    df = data["BTC"].copy()
    df.iloc[500:520, df.columns.get_loc("close")] = np.nan  # poison 20 rows
    data["BTC"] = df
    res = Backtester(data, make_cfg(), funding=funding).run()
    assert np.isfinite(res.equity).all()  # N/A rows filtered, run survives


def test_asset_without_history_is_excluded():
    data, funding = generate_market(n_bars=2600, seed=13)
    data["HYPE"] = data["HYPE"].iloc[-100:]  # under min_history_bars
    res = Backtester(data, make_cfg(), funding=funding).run()
    assert "HYPE" not in res.stats["assets"]
