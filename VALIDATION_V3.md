# VALIDATION (V3) — §6 gates adapted to the continuous portfolio system

Period: 2025-01-01 .. 2026-07-08 (4H bars, params fixed at frozen v3 values, equity0 = 100,000)
Assets: BTC, ETH, SOL, HYPE

## Gate 1 — Walk-forward (fixed params): PASS
- total return +38.21%, max DD -6.04%, ann vol 10.1%, sharpe 2.16, turnover 51.3x/yr, fees 4,171, net funding -243
- quarterly segments (return / max DD):
  - 2025Q1: -4.96% / -5.10%
  - 2025Q2: +4.24% / -3.23%
  - 2025Q3: +9.03% / -3.43%
  - 2025Q4: -0.67% / -4.69%
  - 2026Q1: +8.74% / -5.28%
  - 2026Q2: +20.15% / -2.76%
  - 2026Q3: -1.24% / -1.29%

## Gate 2 — Sensitivity +-20% (8 corners): PASS
- xs_look x0.8 | vol_budget x0.8 | band x0.8: return +21.85%, MDD -8.00%, sharpe 1.58
- xs_look x0.8 | vol_budget x0.8 | band x1.2: return +18.32%, MDD -7.02%, sharpe 1.45
- xs_look x0.8 | vol_budget x1.2 | band x0.8: return +18.62%, MDD -11.68%, sharpe 1.02
- xs_look x0.8 | vol_budget x1.2 | band x1.2: return +19.56%, MDD -9.71%, sharpe 1.09
- xs_look x1.2 | vol_budget x0.8 | band x0.8: return +33.70%, MDD -6.10%, sharpe 2.29
- xs_look x1.2 | vol_budget x0.8 | band x1.2: return +32.13%, MDD -4.81%, sharpe 2.32
- xs_look x1.2 | vol_budget x1.2 | band x0.8: return +59.58%, MDD -8.16%, sharpe 2.48
- xs_look x1.2 | vol_budget x1.2 | band x1.2: return +37.90%, MDD -8.17%, sharpe 1.86

## Gate 3 — Cost stress (fees x2, slippage x2): PASS
- total return +23.89%, sharpe 1.51, fees 7,668

## Gate 4 — Monte Carlo stationary bootstrap (2000 runs, ~5d blocks): PASS
- MDD distribution: median -7.27%, 95th pct -12.53% (limit -25.00%)

## VERDICT: ALL GATES PASS
