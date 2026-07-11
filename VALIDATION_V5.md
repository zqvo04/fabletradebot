# VALIDATION (V5) — §6 gates adapted to the continuous portfolio system

Period: 2025-01-01 .. 2026-07-08 (4H bars, params fixed at frozen v3 values, equity0 = 100,000)
Assets: BTC, ETH, SOL, HYPE, ONDO, TAO, HMSTR, ACT, MORPHO, VIRTUAL, PENGU, TRUMP, PI, KAITO, PARTI, XAU, PUMP, XPL, LAB, MMT, ZEC, ALLO, BEAT, LIT

## Gate 1 — Walk-forward (fixed params): PASS
- total return +78.20%, max DD -11.84%, ann vol 17.8%, sharpe 2.23, turnover 61.3x/yr, fees 2,497, net funding -257
- quarterly segments (return / max DD):
  - 2025Q1: -6.33% / -10.32%
  - 2025Q2: +18.94% / -4.30%
  - 2025Q3: +20.83% / -5.39%
  - 2025Q4: +11.69% / -9.81%
  - 2026Q1: -1.77% / -10.11%
  - 2026Q2: +23.71% / -6.54%
  - 2026Q3: -2.66% / -3.82%

## Gate 2 — Sensitivity +-20% (8 corners): PASS
- xs_look x0.8 | vol_budget x0.8 | band x0.8: return +49.70%, MDD -14.79%, sharpe 1.66
- xs_look x0.8 | vol_budget x0.8 | band x1.2: return +49.53%, MDD -13.93%, sharpe 1.71
- xs_look x0.8 | vol_budget x1.2 | band x0.8: return +74.31%, MDD -15.42%, sharpe 2.06
- xs_look x0.8 | vol_budget x1.2 | band x1.2: return +52.41%, MDD -16.36%, sharpe 1.72
- xs_look x1.2 | vol_budget x0.8 | band x0.8: return +48.55%, MDD -17.47%, sharpe 1.51
- xs_look x1.2 | vol_budget x0.8 | band x1.2: return +50.13%, MDD -14.32%, sharpe 1.72
- xs_look x1.2 | vol_budget x1.2 | band x0.8: return +71.75%, MDD -17.08%, sharpe 1.98
- xs_look x1.2 | vol_budget x1.2 | band x1.2: return +58.84%, MDD -13.39%, sharpe 1.80

## Gate 3 — Cost stress (fees x2, slippage x2): PASS
- total return +72.69%, sharpe 2.12, fees 4,966

## Gate 4 — Monte Carlo stationary bootstrap (2000 runs, ~5d blocks): PASS
- MDD distribution: median -13.04%, 95th pct -22.26% (limit -25.00%)

## VERDICT: ALL GATES PASS
