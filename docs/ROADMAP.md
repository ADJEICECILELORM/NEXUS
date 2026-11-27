# Roadmap — phase gates

Each phase has an explicit gate. No phase starts before the previous gate
passes. Evidence lives in `docs/FINDINGS.md`; decisions in `docs/DECISIONS.md`.

## Phase 0 — Foundation ✅
- [x] Live API verification (Gamma / CLOB / Data API v2) — F0
- [x] Stdlib-only clients: gzip, retries, tolerant normalization
- [x] Landscape survey of OSS Polymarket agents — RESEARCH.md
- [x] poly-agent evidence synthesis (LLM↔market agreement)

## Phase 1 — Research instrument ✅
- [x] SQLite store: resolved markets + winner labels + price points
- [x] Collector (keyset pagination, after_cursor regression-tested)
- [x] negRisk basket scan → F1 KILL (static arb absent)
- [x] Calibration analysis → F2 (Brier 0.0882; 31/35 cells in CI)
- [x] H-B1 pre-registered backtest → F3 OOS PASS at lag=7d
- [x] D4/D5 capacity snapshot → F4 (cost assumption audited; size-limited)
- [x] H-A2 dormant-tail measurement → F5 (maker zone surface)

## Phase 2 — Paper engine (in progress)
- [x] Deterministic book-walking fill engine with fee model
- [x] H-B1 paper harness: daily scan of ≥0.97 zone, entries at 7d-before-end
- [x] Decision-time book snapshots recorded (replaces assumed costs)
      — first live pass 2026-11-27: 4 entries, half-spreads 0.019–0.023
- [ ] 30-day minimum paper record before any further claim
- [ ] ORACLE calibration shadow (LLM forecasts logged, zero authority)

## Phase 3 — Agent layer (blocked by Phase 2)
- [ ] 3 roles only: ORACLE (calibrated signal feed), MIDAS (maker zone),
      AEGIS (risk governance)
- [ ] ORACLE authority gate: Brier < market's on ≥100 live forecasts

## Phase 4 — Execution (off by default)
- [ ] Requires: Phase 2 record + operator signoff + wallet custody decision
- [ ] Default state: DISABLED. This repo currently contains no credentials
      and no order-submission code.
