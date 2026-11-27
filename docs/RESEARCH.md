# Research — API surface and landscape survey

## Verified API surface (live checks, F0)

| endpoint | notes |
|---|---|
| `gamma-api.polymarket.com/markets` | offset pagination; `order=volumeNum` OK; offset capped ~2100 |
| `gamma-api.polymarket.com/markets/keyset` | offset REJECTED (422); continuation param is **`after_cursor`** (docs-confirmed); `cursor` silently ignored (page2==page1) |
| `gamma-api.polymarket.com/events` | limit >50 REQUIRES gzip; `order=volumeNum` not accepted (422) |
| `clob.polymarket.com/ok` | plain text "OK" (not JSON) |
| `clob.polymarket.com/book` | anonymous full depth |
| `clob.polymarket.com/price` | side capitalized: Buy/Sell |
| `clob.polymarket.com/midpoint` | anonymous |
| `data-api.polymarket.com/v2/prices-history` | v1 RETIRED (410); params: token_id, interval ("max"), bucket_seconds, start, end, limit, cursor; 12h buckets on old data; error bodies gzipped |

Market facts: `conditionId` = market; `clobTokenIds` = per-outcome tokens
(usually string-encoded JSON); `outcomePrices` are final 1/0 on resolved
markets; `orderPriceMinTickSize` 0.01/0.001; `orderMinSize` 5 (USD);
`feesEnabled` + `feeSchedule {rate: 0.05, takerOnly: true, rebateRate: 0.25}`
on newer markets; `negRisk` flag; `umaResolutionStatus(es)`.

Fee model used throughout: taker fee = 0.05 × min(p, 1−p) per share.

## Landscape survey (open-source Polymarket agents)

Surveyed 7 projects: Polymarket/agents (official), poly-agent, PolymarketBot,
polymarket-bot (Rust), polybot, CloddsBot, polymarket-agent. Common patterns:
thin wrapper around Gamma + LLM, "yes-bias" portfolios, no pre-registration,
no cost model, no out-of-sample discipline. The only published quantified
study found:

**poly-agent THESIS.md (key external evidence):**
- LLM agent vs market agreement within 5% on **96% of n=104 trials**
- post-Claude-4, agent Brier slightly WORSE than market price on $1M+ markets
- negative PnL driven by Kelly sizing on near-neutral calibration
- claimed alpha zone: **$10K–$500K volume, non-sports, post-cutoff markets**
  (contamination caveat: only post-cutoff markets are valid LLM backtests)

## Implications for NEXUS

1. LLM-as-oracle has no demonstrated edge on liquid markets → ORACLE gets
   zero trade authority until its calibration beats the market's.
2. The H-B1 carry zone (≥0.97, 7d before end) lives near poly-agent's
   claimed thin zone — consistent with our F3/F4 evidence.
3. Maker-side liquidity provision (H-A2 dormant tail) is where the surveyed
   projects never looked — measured in F5, unproven as a strategy.
