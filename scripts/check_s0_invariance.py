"""S0 invariance: whale design-window replay must be unchanged by the instrumentation.

Needs data/ (so not pytest). Run from repo root: python3 scripts/check_s0_invariance.py
Also prints the S0-field profile of SL trades (informational).
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

import exp_stop_common as c

prep = c.load()
r = c.row(prep, c.BASE)
print({k: v for k, v in r.items() if k != "trades"})
assert (r["ret"], r["n"], r["avg_r"]) == (50.6369, 470, 0.2054), (r["ret"], r["n"], r["avg_r"])
print("INVARIANT OK: total_return 50.6369, n 470, avg_r 0.2054")

t = r["trades"]
sl = t[t["reason"] == "SL"]
print(f"\nSL trades: {len(sl)} / {len(t)}")
print(sl[["stop_atr", "mae_r", "bars_to_peak", "peak_r"]].describe(
    percentiles=[.1, .25, .5, .75, .9]).round(3))
print(f"share peak_r <= 0.1: {(sl['peak_r'] <= 0.1).mean():.3f}")
