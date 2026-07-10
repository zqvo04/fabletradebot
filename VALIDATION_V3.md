# VALIDATION (V3) — §6 gates adapted to the continuous portfolio system

Period: 2025-01-01 .. 2026-07-08 (4H bars, params fixed at frozen v3 values, equity0 = 100,000)
Assets: BTC, ETH, SOL, HYPE

## Gate 1 — Walk-forward (fixed params): PASS
- total return +42.06%, max DD -6.11%, ann vol 10.2%, sharpe 2.55, turnover 52.1x/yr, fees 4,001, net funding -252
- quarterly segments (return / max DD):
  - 2025Q1: -0.37% / -0.45%
  - 2025Q2: +4.21% / -3.20%
  - 2025Q3: +6.96% / -3.44%
  - 2025Q4: -0.72% / -4.75%
  - 2026Q1: +8.74% / -5.33%
  - 2026Q2: +20.20% / -2.76%
  - 2026Q3: -1.24% / -1.29%

## Gate 2 — Sensitivity +-20% (8 corners): PASS
- xs_look x0.8 | vol_budget x0.8 | band x0.8: return +24.12%, MDD -8.03%, sharpe 1.84
- xs_look x0.8 | vol_budget x0.8 | band x1.2: return +20.15%, MDD -6.92%, sharpe 1.69
- xs_look x0.8 | vol_budget x1.2 | band x0.8: return +23.98%, MDD -11.66%, sharpe 1.36
- xs_look x0.8 | vol_budget x1.2 | band x1.2: return +23.22%, MDD -9.90%, sharpe 1.37
- xs_look x1.2 | vol_budget x0.8 | band x0.8: return +37.11%, MDD -6.24%, sharpe 2.64
- xs_look x1.2 | vol_budget x0.8 | band x1.2: return +33.29%, MDD -4.81%, sharpe 2.56
- xs_look x1.2 | vol_budget x1.2 | band x0.8: return +60.68%, MDD -7.99%, sharpe 2.68
- xs_look x1.2 | vol_budget x1.2 | band x1.2: return +44.07%, MDD -8.18%, sharpe 2.25

## Gate 3 — Cost stress (fees x2, slippage x2): PASS
- total return +27.51%, sharpe 1.86, fees 7,334

## Gate 4 — Monte Carlo stationary bootstrap (2000 runs, ~5d blocks): PASS
- MDD distribution: median -6.42%, 95th pct -11.18% (limit -25.00%)

## VERDICT: ALL GATES PASS
