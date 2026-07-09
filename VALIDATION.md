# VALIDATION — BLUEPRINT §6 gates on real OKX data

Period: 2025-01-01 .. 2026-07-08 (1H bars, params fixed at spec values, equity0 = 100,000)
Assets: BTC, ETH, SOL, HYPE

## Gate 1 — Walk-forward (fixed params): FAIL
- total return -3.03%, max DD -14.39%, trades 396, win rate 39.4%, avg R +0.001, profit factor 0.94
- quarterly segments (return / max DD):
  - 2025Q1: +0.00% / +0.00%
  - 2025Q2: +0.29% / -3.48%
  - 2025Q3: +0.86% / -3.58%
  - 2025Q4: -3.99% / -5.34%
  - 2026Q1: -2.53% / -4.71%
  - 2026Q2: +3.44% / -8.09%
  - 2026Q3: -0.96% / -1.42%
- by playbook:
  - P1: n=166, win=36%, avgR=-0.02, sumR=-3.6
  - P2: n=155, win=38%, avgR=+0.01, sumR=+1.0
  - P3: n=64, win=52%, avgR=+0.02, sumR=+1.6
  - P4: n=11, win=45%, avgR=+0.14, sumR=+1.5

## Gate 2 — Sensitivity +-20% (8 corners): PASS
- theta x0.8 | ER x0.8 | ATR x0.8: return -14.90%, MDD -14.98%, trades 596
- theta x0.8 | ER x0.8 | ATR x1.2: return -14.44%, MDD -15.47%, trades 678
- theta x0.8 | ER x1.2 | ATR x0.8: return -14.91%, MDD -15.37%, trades 418
- theta x0.8 | ER x1.2 | ATR x1.2: return -14.50%, MDD -15.03%, trades 404
- theta x1.2 | ER x0.8 | ATR x0.8: return -11.76%, MDD -14.49%, trades 318
- theta x1.2 | ER x0.8 | ATR x1.2: return -2.38%, MDD -13.79%, trades 352
- theta x1.2 | ER x1.2 | ATR x0.8: return -11.73%, MDD -12.30%, trades 218
- theta x1.2 | ER x1.2 | ATR x1.2: return -2.67%, MDD -8.52%, trades 210

## Gate 3 — Cost stress (fees x2, slippage x2): FAIL
- total return -14.69%, avg R -0.104, trades 218, profit factor 0.52

## Gate 4 — Monte Carlo sequence shuffle (2000 runs): PASS
- MDD distribution: median -10.37%, 95th pct -14.47% (limit -25.00%)

## VERDICT: GATES FAILED — do not deploy
