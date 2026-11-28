# H-B1 zone: measured executable costs

Source: decision-time book snapshots from the paper harness journal
(`nexus.paper.hb1_harness`). Costs the D2 backtest assumed vs. costs
the market actually quotes. Updated as the harness accumulates days.

D2 assumed half-spread: **0.002** (pre-registered).

## Overall

| metric | n | min | median | mean | max |
|---|---|---|---|---|---|
| half-spread | 4 | 0.0045 | 0.01975 | 0.016875 | 0.0235 |
| $100 slippage | 4 | 0.005344 | 0.01975 | 0.017086 | 0.0235 |
| ask depth (USD) | 4 | 1079.0 | 7218.115 | 26114.875 | 88944.27 |

Decisions: 4 | entered: 4 | rejected: 0

## By day

| day | decisions | median half-spread | median $100 slip | median depth |
|---|---|---|---|---|
| 2026-11-27 | 1 | 0.0215 | 0.0215 | 13024.69 |
| 2026-11-28 | 3 | 0.018 | 0.018 | 1411.54 |

Median half-spread is **9.9x** the D2 assumption. If this persists as n grows, a labeled D-amendment re-grades F3 at measured costs per the D4/D5 pattern - no claims move until then.

