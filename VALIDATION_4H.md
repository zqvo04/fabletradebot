# VALIDATION (4H) — BLUEPRINT §6 gates on real OKX data

Period: 2025-01-01 .. 2026-07-08 (4H bars, params fixed at spec values, equity0 = 100,000)
Assets: BTC, ETH, SOL, HYPE

## Gate 1 — Walk-forward (fixed params): PASS
- total return +1.80%, max DD -5.00%, trades 87, win rate 36.8%, avg R +0.030, profit factor 1.18
- quarterly segments (return / max DD):
  - 2025Q1: +0.00% / +0.00%
  - 2025Q2: +0.66% / -2.17%
  - 2025Q3: +2.19% / -1.94%
  - 2025Q4: +0.32% / -0.90%
  - 2026Q1: -1.41% / -2.38%
  - 2026Q2: +0.26% / -2.38%
  - 2026Q3: -0.20% / -0.23%
- by playbook:
  - P1: n=41, win=41%, avgR=+0.17, sumR=+7.2
  - P2: n=32, win=31%, avgR=-0.09, sumR=-2.8
  - P3: n=7, win=29%, avgR=-0.14, sumR=-1.0
  - P4: n=7, win=43%, avgR=-0.12, sumR=-0.8

## Gate 2 — Sensitivity +-20% (8 corners): PASS
- theta x0.8 | ER x0.8 | ATR x0.8: return +2.61%, MDD -6.25%, trades 185
- theta x0.8 | ER x0.8 | ATR x1.2: return +0.06%, MDD -7.61%, trades 181
- theta x0.8 | ER x1.2 | ATR x0.8: return +1.41%, MDD -6.17%, trades 203
- theta x0.8 | ER x1.2 | ATR x1.2: return +0.13%, MDD -6.76%, trades 197
- theta x1.2 | ER x0.8 | ATR x0.8: return +2.44%, MDD -3.65%, trades 81
- theta x1.2 | ER x0.8 | ATR x1.2: return +0.09%, MDD -5.46%, trades 80
- theta x1.2 | ER x1.2 | ATR x0.8: return +3.94%, MDD -3.28%, trades 49
- theta x1.2 | ER x1.2 | ATR x1.2: return +3.68%, MDD -3.63%, trades 48

## Gate 3 — Cost stress (fees x2, slippage x2): PASS
- total return +0.88%, avg R +0.021, trades 87, profit factor 1.08

## Gate 4 — Monte Carlo sequence shuffle (2000 runs): PASS
- MDD distribution: median -3.05%, 95th pct -4.59% (limit -25.00%)

## VERDICT: ALL GATES PASS
