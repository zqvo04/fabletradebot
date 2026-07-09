# VALIDATION (4H) — BLUEPRINT §6 gates on real OKX data

Period: 2025-01-01 .. 2026-07-08 (4H bars, params fixed at spec values, equity0 = 100,000)
Assets: BTC, ETH, SOL, HYPE

## Gate 1 — Walk-forward (fixed params): PASS
- total return +1.91%, max DD -4.93%, trades 87, win rate 36.8%, avg R +0.032, profit factor 1.19
- quarterly segments (return / max DD):
  - 2025Q1: +0.00% / +0.00%
  - 2025Q2: +0.68% / -2.16%
  - 2025Q3: +2.21% / -1.93%
  - 2025Q4: +0.34% / -0.90%
  - 2026Q1: -1.38% / -2.37%
  - 2026Q2: +0.28% / -2.37%
  - 2026Q3: -0.20% / -0.23%
- by playbook:
  - P1: n=41, win=41%, avgR=+0.18, sumR=+7.3
  - P2: n=32, win=31%, avgR=-0.09, sumR=-2.7
  - P3: n=7, win=29%, avgR=-0.14, sumR=-1.0
  - P4: n=7, win=43%, avgR=-0.12, sumR=-0.8

## Gate 2 — Sensitivity +-20% (8 corners): PASS
- theta x0.8 | ER x0.8 | ATR x0.8: return +2.77%, MDD -6.17%, trades 185
- theta x0.8 | ER x0.8 | ATR x1.2: return +0.21%, MDD -7.51%, trades 181
- theta x0.8 | ER x1.2 | ATR x0.8: return +1.48%, MDD -6.16%, trades 203
- theta x0.8 | ER x1.2 | ATR x1.2: return +0.22%, MDD -6.72%, trades 197
- theta x1.2 | ER x0.8 | ATR x0.8: return +2.56%, MDD -3.58%, trades 81
- theta x1.2 | ER x0.8 | ATR x1.2: return +0.20%, MDD -5.39%, trades 80
- theta x1.2 | ER x1.2 | ATR x0.8: return +4.03%, MDD -3.23%, trades 49
- theta x1.2 | ER x1.2 | ATR x1.2: return +3.77%, MDD -3.59%, trades 48

## Gate 3 — Cost stress (fees x2, slippage x2): PASS
- total return +1.12%, avg R +0.025, trades 87, profit factor 1.10

## Gate 4 — Monte Carlo sequence shuffle (2000 runs): PASS
- MDD distribution: median -2.98%, 95th pct -4.49% (limit -25.00%)

## VERDICT: ALL GATES PASS
