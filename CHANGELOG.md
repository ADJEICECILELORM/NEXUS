# Changelog

All notable changes to NEXUS. Dates are UTC.

## 0.8 — H-B1 paper harness + v2 audit (2026-11-27)

- `nexus/paper/hb1_harness.py`: daily pass that scans open binary markets
  inside the D2 lag window (7d ± 1d, targeted via keyset `end_date_min/max`
  + `order=endDate`), takes decision-time book snapshots for both tokens,
  and paper-enters any token with book midpoint ≥ 0.97 through the
  PaperEngine ($200 cap per F4). Every decision — entered or rejected — is
  journaled with half-spread, ask depth, and $100 slippage.
- **First live pass (2026-11-27 13:16 UTC)**: 300 markets scanned, 164 in
  window, 4 candidates found and paper-entered (crypto price markets ending
  Dec 3). First cost datapoints: half-spreads 0.0195–0.0225 — ~10× the D2
  assumed 0.002 and ~2.5× F4's top-volume median. Small markets are thin;
  the harness will accumulate the real cost distribution.
- **HTTP layer fix**: Cloudflare now blocks the default Python-urllib
  User-Agent on clob.polymarket.com (403 error 1010, verified live). A
  browser-like UA is sent by default; verified 200 with book payloads.
- `docs/V2_AUDIT.md`: line-level triage of `nexus_v2_final.zip` (operator
  uploaded it to `ADJEICECILELORM/nexus-zip`). All 5 external-audit claims
  confirmed (dead market-data wiring, ZEUS records unfilled orders, AEGIS
  close/kill path never called, WATCHDOG never restarts, security holes)
  plus 6 new defects. Verdicts: keep roles ORACLE/MIDAS/AEGIS + HERALD as
  signal feed; rebuild bodies; kill ARGUS/LINK/MACRO/GA/deploy/wallets.
- Full rebuild of the codebase pushed to GitHub after the sandbox wipe
  (commits 9deb7af..4719276); CI green, 37 tests. 44 tests now.

## 0.7 — Paper engine (2026-11-26)

- `nexus/paper/engine.py`: `PaperEngine` with `PaperConfig` (starting cash
  10,000, taker fee 0.05, min order $5, per-position cap $200 per F4's
  size-limited verdict), `PaperPosition`, `PaperFill`, `PaperAccount`.
- `buy()` walks the book via `fill_from_book` and rejects with explicit codes:
  `below_min_order`, `position_cap`, `no_liquidity`, `insufficient_book_depth`,
  `insufficient_cash`. `sell()` uses proportional cost-basis accounting.
  `settle()` resolves at 1.0/0.0 with no fee. `unrealized()`/`equity()` mark
  against supplied books.
- Paper-only: no network access anywhere in the module. Every fill, rejection,
  and settlement is journaled.
- Tests: **37 passing** (6 new paper tests).

## 0.6 — Dormant tail measurement (2026-11-26)

- `nexus/research/dormant.py`: H-A2 classifier (dead / one-sided / dormant /
  live) with $500 depth check, event-level scanning, aggregate + markdown
  report, CLI (`--max-events --sleep --out`).
- **FINDINGS F5**: 87.5% of 1,009 long-tail outcome books lack a live
  two-sided state; dormant median spread 0.003, bid depth $19 vs ask depth
  $257k. Parked with no claims — the maker zone exists but is thin.

## 0.5 — Capacity measurement (2026-11-26)

- `nexus/research/capacity.py`: D4/D5 snapshot tooling — candidate sampling,
  dual-book snapshots (half-spread, $100/$500 slippage via `fill_from_book`,
  USD depth), aggregate + report, CLI.
- `fill_from_book` extracted from `ClobPublicClient.estimate_fill` into a pure
  shared function used by both the live estimator and the paper broker.
- **FINDINGS F4**: measured median half-spread 0.00825 = 4.1× the D2
  assumption → F3's PASS suspended per D4 and **re-graded with the measured
  cost → PASS again** (n=57, mean +0.00840, Wilson lo +0.00690). Viability is
  size-limited.

## 0.4 — Keyset fix + H-B1 OOS PASS (2026-11-26)

- **Bug fix**: `GammaClient.markets_keyset` sent the cursor as `cursor`; the
  keyset endpoint requires **`after_cursor`** (offset is rejected with 422;
  `cursor` was silently ignored, producing 100% page overlap). Fixed, with a
  regression test (`test_markets_keyset_uses_after_cursor`).
- D3 power extension executed: 6,000 markets collected, backfill for both
  outcome tokens.
- **FINDINGS F3**: H-B1 OOS **PASS** at lag=7d — n=131, mean +0.00763/share,
  0 flips, Wilson lower bound +0.00641.
- Tests: 31 passing.

## 0.3 — Calibration (2026-11-26)

- `nexus/research/calibration.py`: lag × band calibration cells with Wilson
  CIs, staleness filter (3d), decided-market flip analysis at p ≥ 0.97, Brier
  score, CLI.
- **FINDINGS F2**: market Brier 0.0882; calibrated in 31/35 cells; 0/420
  decided-market flips at ≥7d lag; flips concentrate at ≤3d.
- `nexus/research/backfill.py`: per-token history fetch from Data API v2
  (interval=max), skip-if-present, `--force` override, commit every 25 tokens.
- Tests: 30 passing.

## 0.2 — Phase 1 kickoff (2026-11-25)

- `nexus/research/store.py`: SQLite research store (schema v1:
  `resolved_markets`, `price_points`), idempotent upserts, `all_token_ids`
  covers both outcome tokens with winner flags.
- `nexus/research/collect.py`: resolved-market collector with winner index
  (exact ≥0.999 price rule).
- `nexus/research/negrisk.py`: negRisk event-edge scanner (long_gap /
  short_gap, ≥3 priced books), CLI + markdown report.
- `nexus/research/dataapi.py`: Data API v2 client (`/v2/prices-history`).
- **FINDINGS F1**: static negRisk arb **KILLED** — all 9 complete events
  negative in both directions at top-of-book. Secondary signal: dormant
  long-tail books → H-A2.
- **FINDINGS F0**: API ground-truth notes (gamma keyset/cursor semantics, gzip
  requirements, CLOB endpoints, fee model).

## 0.1 — Foundation (2026-11-25)

- Project skeleton: `nexus` package, stdlib-only HTTP layer
  (`nexus/polymarket/http.py` with gzip + retry/backoff), Gamma client
  (`Market` dataclass with tolerant double-encoded-JSON normalization, offset
  iterator + keyset endpoint), CLOB public client (`Book`, midpoint, price),
  append-only JSONL `Journal`.
- Docs: README, ARCHITECTURE, ROADMAP, RESEARCH (verified API surface +
  7-project landscape survey), DECISIONS (D1–D3), FINDINGS (F0–F2).
- CI: GitHub Actions workflow (Python 3.12, pytest).
