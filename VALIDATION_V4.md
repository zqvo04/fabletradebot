# VALIDATION (V4) — §6 gates adapted to the continuous portfolio system

Period: 2025-01-01 .. 2026-07-08 (4H bars, params fixed at frozen v3 values, equity0 = 100,000)
Assets: BTC, ETH, SOL, HYPE

## Gate 1 — Walk-forward (fixed params): PASS
- total return +98.34%, max DD -12.94%, ann vol 19.5%, sharpe 2.41, turnover 101.1x/yr, fees 9,375, net funding -604
- quarterly segments (return / max DD):
  - 2025Q1: -2.48% / -2.60%
  - 2025Q2: +6.91% / -6.13%
  - 2025Q3: +15.80% / -7.24%
  - 2025Q4: -3.12% / -9.02%
  - 2026Q1: +22.89% / -8.36%
  - 2026Q2: +42.14% / -6.03%
  - 2026Q3: -2.58% / -2.67%

## Gate 2 — Sensitivity +-20% (8 corners): PASS
- xs_look x0.8 | vol_budget x0.8 | band x0.8: return +33.68%, MDD -17.56%, sharpe 1.25
- xs_look x0.8 | vol_budget x0.8 | band x1.2: return +40.05%, MDD -14.46%, sharpe 1.48
- xs_look x0.8 | vol_budget x1.2 | band x0.8: return +53.40%, MDD -22.32%, sharpe 1.34
- xs_look x0.8 | vol_budget x1.2 | band x1.2: return +73.93%, MDD -17.32%, sharpe 1.75
- xs_look x1.2 | vol_budget x0.8 | band x0.8: return +103.67%, MDD -9.87%, sharpe 2.84
- xs_look x1.2 | vol_budget x0.8 | band x1.2: return +71.32%, MDD -9.70%, sharpe 2.36
- xs_look x1.2 | vol_budget x1.2 | band x0.8: return +180.88%, MDD -13.63%, sharpe 2.86
- xs_look x1.2 | vol_budget x1.2 | band x1.2: return +103.42%, MDD -15.39%, sharpe 2.16

## Gate 3 — Cost stress (fees x2, slippage x2): PASS
- total return +61.37%, sharpe 1.77, fees 16,514

## Gate 4 — Monte Carlo stationary bootstrap (2000 runs, ~5d blocks): PASS
- MDD distribution: median -12.61%, 95th pct -20.87% (limit -25.00%)

## VERDICT: ALL GATES PASS
