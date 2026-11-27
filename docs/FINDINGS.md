# Research Findings

Running log of empirical results. Newest at top. Every claim is tied to a
command that reproduces it and to live data.

## F5 — H-A2 dormant tail: the maker zone exists, but it is sand, not gold (2026-11-26)

Reproduce: `python -m nexus.research.dormant --max-events 30` (measurement
only per D1 — no strategy claim; snapshot: `data/dormant_snapshot.json`).

30 open negRisk events, 1,009 outcome books (books fetched live; HTTP 404
"No orderbook exists" classified as no-orderbook, not error):

| status | count | share |
|---|---|---|
| no-orderbook (never attracted any resting order) | 581 | 57.6% |
| dormant (two-sided, one side < $500 resting) | 166 | 16.5% |
| one-sided | 136 | 13.5% |
| live (two-sided, both sides >= $500) | 126 | 12.5% |

Dormant outcomes (the maker-zone candidates):

| metric | median | p90 |
|---|---|---|
| available spread (maker gross capture per round trip) | 0.0030 | 0.1000 |
| bid-side resting depth | $19.01 | — |
| ask-side resting depth | $257,062 | — |

Live outcomes: median spread 0.0010. Median family dormant fraction: 23.7%.

**Reading:**
1. The dormant tail is REAL and dominant — 87.5% of long-tail outcomes lack
   a live two-sided book; 58% have none at all. This corroborates F1's
   secondary signal and the H-A2 premise.
2. BUT the depth structure is a wall of asks over a floor of bids ($19 bid
   vs $257k ask at the median dormant outcome). Anyone resting bids there
   is the ONLY bidder — fill risk on family-wide news is total, not marginal.
3. Median gross capture is 0.3% per round trip; p90 10%. The fat tail exists
   but is, by construction, where adverse selection concentrates.
4. NOT measured here (deliberately): adverse-selection cost under news
   shocks. That requires the paper phase with actual resting quotes and
   event-time observation. H-A2 remains an open hypothesis — now with a
   measured surface, still zero claims.

**Status: F5 recorded. H-A2 stays parked pending paper-phase quoting
experiments. No strategy claim.**

## F4 — Capacity snapshot: cost assumption validated at median, viability is size-limited (2026-11-26)

Reproduce: `python -m nexus.research.capacity` (protocol: DECISIONS D4,
amended by D5 BEFORE the qualifying run; snapshot: `data/capacity_snapshot.json`).

First execution (D4 as written) returned INSUFFICIENT by its own rule
(2 qualifying books < 30) — the protocol had checked only the YES token,
excluding half the zone by construction. D5 (labeled post-hoc) fixed zone
qualification to both tokens and extended the pool to 1,000 candidates.
Amended run: 956 candidates sampled, 893 midpoints checked, **36 qualifying
markets / 72 books** (≥30 bar met). All 36 markets fee-enabled (taker 0.05×
min(p,1−p)).

| metric | median | p90 | max |
|---|---|---|---|
| top-of-book half-spread | 0.00825 | 0.02400 | 0.02750 |
| slippage, $100 buy | 0.01762 | 0.50395 | — |
| slippage, $500 buy | 0.10755 | 0.73574 | — |
| whole-book bid depth (USD) | 279.73 | — | — |
| whole-book ask depth (USD) | 3,863.64 | — | — |

**Mechanical verdict per D4: median half-spread 0.00825 > assumed 0.002 →
F3's PASS was SUSPENDED and H-B1 re-graded with the measured median
substituted into the same D2 backtest.** Re-grade result (D2 criteria
unchanged):

| cost | OOS n | mean pnl/share | flips | wilson lo | verdict |
|---|---|---|---|---|---|
| 0.002 (D2 assumed) | 131 | +0.00763 | 0 | +0.00641 | PASS (F3) |
| **0.00825 (measured median)** | **57** | **+0.00840** | **0** | **+0.00690** | **PASS — F3 reinstated** |
| 0.0176 (median $100 slippage) | 28 | +0.00460 | 0 | +0.00340 | INSUFFICIENT (n<30) |
| 0.024 (p90 half-spread) | 6 | +0.00404 | 0 | +0.00244 | INSUFFICIENT (n<30) |

Why the mean IMPROVES as spread rises: higher fill cost filters out fills at
p ≥ 1 − spread (`fill >= 1.0` guard) — the most expensive, least profitable
trades drop out; survivors are strictly cheaper entries. The tradeable
sample shrinks from 131 to 57, so per-year trade frequency roughly halves.

**Reading (honest):**
1. H-B1 survives its own cost audit at the measured median: PASS with
   positive Wilson lower bound, 0 flips, four consecutive positive years.
2. Viability is SIZE-LIMITED. At median $100-order slippage the sample falls
   below the power bar; at p90 spread the zone is untradeable. Median
   whole-book depth ($280 bid / $3.9k ask) means orders must stay small and
   likely rest passively (maker), not take.
3. The p90 $100 slippage of 0.50 confirms hollow one-sided books exist
   inside the zone (direct H-A2 evidence: dormant tails sit next to live
   favorites).
4. Cross-sectional snapshot of one moment — the historical re-grade still
   uses the D2 cost path; only paper trading can validate realized costs.

**Status: H-B1 = pre-registered PASS, capacity-gated to small size. Next
sanctioned step: paper engine with book-walking fills + measured cost
recording. No deployment.**

## F3 — H-B1 decided-market carry: OOS PASS at lag=7d (2026-11-26)

Reproduce: `python -m nexus.research.hb1_backtest` (repo root; DB:
`data/nexus_research.db`). Pre-registration: `docs/DECISIONS.md` D2; dataset
extension: D3 (criteria unchanged).

**Fix applied first:** Gamma keyset pagination was silently broken — the
continuation param is `after_cursor`, not `cursor` (docs-confirmed; with
`cursor` the server echoes the same page forever: 100% overlap). After the
fix, consecutive pages show 0% ID overlap. Dataset: 6,998 resolved markets
(6,628 with winner labels), 2,325 tokens with price history, 0 fetch
failures.

| lag | seg | n | mean pnl/share | flips | flip rate | win rate | wilson lo |
|---|---|---|---|---|---|---|---|
| 3d | OOS | 134 | -0.02150 | 4 | 0.030 | 0.970 | -0.05009 |
| **7d** | **OOS** | **131** | **+0.00763** | **0** | **0.000** | **1.000** | **+0.00641** |
| 14d | OOS | 135 | -0.01439 | 3 | 0.022 | 0.978 | -0.03918 |
| 30d | OOS | 162 | -0.00457 | 2 | 0.012 | 0.988 | -0.02152 |

Cost model (pre-registered D2): fill = price + 0.002 half-spread + taker fee
0.05×min(p, 1−p); split IS 70%/OOS 30% by end_date (split_ts=1675296000 ≈
Feb 2023); honest labels (loser tokens bought at ≥0.97 resolve to 0).

**Mechanical verdict per D2: PASS.** Primary (lag=7d, p≥0.97) OOS: n=131
(≥30), mean +0.00763/share > 0, flip rate 0% < 2%, Wilson 95% lower bound
+0.00641 > 0. All three pass conditions met simultaneously.

Robustness diagnostics (not part of the verdict; for honesty):

- OOS spread across four end-years, positive in each: 2023 +0.0127 (n=26),
  2024 +0.0083 (n=13), 2025 +0.0062 (n=43), 2026 +0.0060 (n=49).
- Zero losing trades in OOS (min pnl/share +0.0005); zero wipeouts.
- Edge is not top-heavy: mean excluding the top 5% of winners is still
  +0.00669; median +0.0048.

**Caveats (read before believing PASS):**

1. **Lag fragility:** every non-primary lag is OOS-negative, and the 3d
   (3.0%) and 14d (2.2%) flip rates would trip the 2% kill threshold. A ±1
   change in the pre-registered lag flips the verdict. The edge exists only
   in the exact 7d configuration — deployment (if ever) must anchor to it,
   and paper trading must verify it live, not assume it transfers.
2. **IS segment is degenerate (n=3)** — the Data API's history doesn't reach
   most pre-2023 markets, so almost everything graded as OOS. Nothing was
   tuned on IS (thresholds/lags/costs were fixed in D2 before any run), so
   there is no fitting leakage — but this also means there is no true IS
   confirmation, just the one OOS segment.
3. **Data resolution:** old markets have 12h price buckets and the staleness
   window is 3.0 days, so the "7d" print is actually a price observed
   ~4–10 days before end. Fine for carry measurement; imprecise for timing.
4. **Cost model is an assumption** (pre-registered, not measured): half-
   spread 0.002 and fee 0.05×min(p,1−p). Real decision-time books must be
   measured before any paper-trading claim (next step).
5. **Capacity unknown:** ~131 OOS trades over ~4 years ≈ 33/year at this
   threshold on the top-6,000-by-volume closed markets; median edge 0.48¢/share
   and orderMinSize $5. Profitable only if meaningful size can rest at/near
   these prices. Measure real depth before sizing anything.

**Status: H-B1 graduates from KILLED/INSUFFICIENT to pre-registered PASS.
Next gate (capacity): decision-time book snapshots at ≥0.97 markets
(H-A2 measurement shares this data need). No deployment; paper engine is
the only sanctioned next step.**

## F2 — Market calibration: well-calibrated; flips concentrate at ≤3d (2026-11-26)

Reproduce: `python -m nexus.research.calibration` (DB: 1,000+ resolved
markets by volume, both outcome tokens backfilled — selection-bias-free).

Dataset: 998 resolved markets (volume-desc), 1,993 token histories (BOTH
sides of each market), observations = last price print at or before
(end − L) within a 3-day staleness window.

**Brier score (market price vs realized outcome): 0.0882** pooled across
lags. For reference, always-0.5 scores 0.25; poly-agent reported LLM agents
scoring slightly WORSE than market post-Claude-4 on liquid markets.

Calibration by lag × price band: in 31 of 35 cells the empirical resolution
rate sits INSIDE the Wilson 95% CI around the mean price. The market is well
calibrated on liquid resolved markets — consistent with poly-agent's
evidence (F0/poly-agent) and with H-LLM's premise.

Flags outside CI (4 of 35 cells — ~2 expected by chance, treat as leads only):

| lag | band | n | mean p | rate | note |
|---|---|---|---|---|---|
| 7d | 0.00–0.10 | 163 | 0.017 | 0.043 [+0.021, +0.086] | longshots resolve more than priced |
| 7d | 0.90–0.97 | 35 | 0.936 | 0.800 | near-decided underperform (n too small) |
| 3d | 0.97–1.01 | 140 | 0.996 | 0.979 | 3 flips |
| 1d | 0.97–1.01 | 144 | 0.996 | 0.979 | same 3 markets |

**Decided-market carry (candidate strategy family H-B1):** buying YES at
p ≥ 0.97, L days before end:

| lag | decided (n) | flips | flip rate | binomial 95% upper |
|---|---|---|---|---|
| 30d | 152 | 0 | 0.000 | ~2.4% |
| 14d | 140 | 0 | 0.000 | ~2.6% |
| 7d | 128 | 0 | 0.000 | ~2.9% |
| 3d | 140 | 3 | 0.021 | ~5.3% |
| 1d | 144 | 3 | 0.021 | ~5.3% |

Reading: once a high-volume market prices at 0.97+ **7 or more days out, it
has never flipped in this sample (0/420)** — the risk concentrates in the
last 72 hours (news/deadline events near close). The gross carry is ~0.4–0.5¢
per share at p=0.995; whether that survives taker fees (0.25% of min(p,1-p) ×
size ≈ 0.05% at p=0.995, i.e. half a basis point of notional — small) and
ASK-side spread on deep books is the open question. The 3d/1d cells show the
strategy has a real mode of failure.

**Verdict: H-B1 advances to cost-aware backtest (next step).** Not a claim of
profit. Wide CIs, single-sample risk, and end-date≠resolution-time artifacts
all handled when the walk-forward harness applies fee/spread/size constraints
and out-of-sample splits.

**Corroboration for F1/H-A2:** 1,953 stale price prints excluded near
resolution — the long tail is genuinely dormant; maker-zone measurement with
depth-weighted aggregation remains queued (H-A2).

## F1 — negRisk event-tree arb: absent at top-of-book (2026-11-25)

Reproduce: `python -m nexus.research.negrisk --max-events 40`

Scanned 40 open negRisk events with REAL order books (not the possibly-stale
`outcomePrices`): 9 events had complete level-1 books across all outcomes
(4–10 outcomes each; "How many X in 2026?" families, 100% volume band $95k–$1.7M).

| Direction | Measurement | Result over 9 complete events |
|---|---|---|
| Long (buy every YES) | 1 − sum(best ask) | min −$0.76 · median −$0.12 · max −$0.018 |
| Short (sell every YES) | sum(best bid) − 1 | min −$0.39 · median −$0.10 · max −$0.015 |

**Verdict: no static arb.** Every complete event is priced so that the basket
costs MORE than $1 to buy and receives LESS than $1 to sell — on both sides,
before fees. This is consistent with an efficient event-tree that includes
taker fees (`.feeSchedule rate: 0.05, takerOnly` was live on most scanned
events) plus spread compensation.

**Secondary signal (untested hypothesis, not a strategy):** short-side gaps of
−0.10 to −0.39 mean the deepest part of these books is stale or absent in
long-tail outcomes. Making markets there (posting resting quotes) earns the
spread instead of paying it — but that is a DIFFERENT strategy class with its
own risks (adverse selection when news breaks mid-family). Parked as H-A2;
nothing is assumed.

## F0 — API ground truth (2026-11-25)

All public endpoints verified live from this environment (no auth): Gamma
markets/events, CLOB ok/book/price/midpoint, Data API v2 prices-history.
Full table in `docs/RESEARCH.md`. Notable: v1 Data API retired (HTTP 410);
keyset pagination requires `after_cursor`; `/events` limit>50 requires gzip;
error bodies are gzipped; fee schedule on newer markets is taker-only 5% of
min(p, 1−p).
