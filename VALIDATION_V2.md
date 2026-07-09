# VALIDATION (V2) — BLUEPRINT §6 gates on real OKX data

Period: 2025-01-01 .. 2026-07-08 (V2 bars, params fixed at spec values, equity0 = 100,000)
Assets: BTC, ETH, SOL, HYPE

## Gate 1 — Walk-forward (fixed params): PASS
- total return +8.36%, max DD -6.77%, trades 50, win rate 34.0%, avg R +0.256, profit factor 1.51
- quarterly segments (return / max DD):
  - 2025Q1: +0.00% / +0.00%
  - 2025Q2: +0.83% / -4.01%
  - 2025Q3: +8.85% / -3.05%
  - 2025Q4: -0.48% / -2.19%
  - 2026Q1: -1.10% / -4.25%
  - 2026Q2: +0.33% / -3.69%
  - 2026Q3: -0.03% / -0.10%
- by playbook:
  - P1: n=42, win=33%, avgR=+0.32, sumR=+13.5
  - P4: n=8, win=38%, avgR=-0.09, sumR=-0.7

## Gate 2 — Sensitivity +-20% (8 corners): PASS
- theta x0.8 | ER x0.8 | ATR x0.8: return +11.12%, MDD -4.56%, trades 50
- theta x0.8 | ER x0.8 | ATR x1.2: return +9.54%, MDD -5.61%, trades 49
- theta x0.8 | ER x1.2 | ATR x0.8: return +7.67%, MDD -7.49%, trades 55
- theta x0.8 | ER x1.2 | ATR x1.2: return +5.68%, MDD -6.58%, trades 54
- theta x1.2 | ER x0.8 | ATR x0.8: return +11.55%, MDD -4.69%, trades 36
- theta x1.2 | ER x0.8 | ATR x1.2: return +11.03%, MDD -5.04%, trades 35
- theta x1.2 | ER x1.2 | ATR x0.8: return +8.93%, MDD -6.94%, trades 40
- theta x1.2 | ER x1.2 | ATR x1.2: return +8.21%, MDD -6.40%, trades 39

## Gate 3 — Cost stress (fees x2, slippage x2): PASS
- total return +10.16%, avg R +0.269, trades 50, profit factor 1.54

## Gate 4 — Monte Carlo sequence shuffle (2000 runs): PASS
- MDD distribution: median -4.46%, 95th pct -7.32% (limit -25.00%)

## VERDICT: ALL GATES PASS
