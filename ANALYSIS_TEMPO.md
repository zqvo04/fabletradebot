# ANALYSIS — 1H vs 4H tempo, maker-economics re-run

Design window 2025-01-01..2025-12-31 (selection), holdout 2026-01-01..2026-07-08 (untouched until selection was made).
Only order type and bar size vary; no signal parameter was re-tuned.

| variant | design ret | design MDD | design trades | design avgR | holdout ret | holdout trades | fees (full) |
|---|---|---|---|---|---|---|---|
| 1H-taker | -2.88% | -5.34% | 202 | -0.006 | -0.15% | 194 | 10901 |
| 1H-mExit | -2.11% | -5.20% | 201 | +0.002 | +1.50% | 194 | 10634 |
| 1H-mExit+opt | -2.20% | -5.51% | 201 | -0.000 | +2.20% | 194 | 9823 |
| 1H-mExit+real | -2.19% | -5.33% | 202 | +0.001 | +1.16% | 194 | 9626 |
| 4H-taker | +3.30% | -2.62% | 36 | +0.131 | -1.46% | 51 | 1347 |
| 4H-mExit | +3.36% | -2.61% | 36 | +0.133 | -1.41% | 51 | 1296 |
| 4H-mExit+real | +3.28% | -2.65% | 36 | +0.127 | -2.34% | 51 | 1162 |

## Winner on design window: **4H-mExit**
- holdout (2026, untouched): -1.41%, 51 trades, avg R -0.039
- full-period gates: G1 PASS (+1.91%, MDD -4.93%), G2 PASS, G3 PASS (+1.12%), G4 PASS (95th pct MDD -4.49%)

## Risk-scaling frontier (winner, full period)
| r_base | return | max DD |
|---|---|---|
| 0.75% | +1.91% | -4.93% |
| 1.25% | +3.10% | -8.06% |
| 2.00% | +2.04% | -14.84% |

## FINAL: 4H-mExit — gates ALL PASS
