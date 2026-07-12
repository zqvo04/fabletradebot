

def test_notify_freshness_guard_logic():
    """Deep-history replay events must not re-announce; only events within the
    freshness window of the latest bar do. (Mirrors run_live's is_fresh.)"""
    import pandas as pd
    latest_bar = pd.Timestamp("2026-07-11 11:00", tz="UTC")
    fresh_cut = latest_bar - pd.Timedelta(hours=12)

    def is_fresh(ts):
        return pd.Timestamp(ts) >= fresh_cut

    assert is_fresh("2026-07-11 10:00+00:00")      # 1h old  -> announce
    assert is_fresh("2026-07-11 00:00+00:00")      # 11h old -> announce
    assert not is_fresh("2026-07-10 20:00+00:00")  # 15h old -> absorb
    assert not is_fresh("2026-05-01 00:00+00:00")  # ancient -> absorb
