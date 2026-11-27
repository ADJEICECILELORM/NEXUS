# NEXUS v2 audit — module triage (2026-11-27)

Source: `nexus_v2_final.zip` (operator uploaded to `ADJEICECILELORM/nexus-zip`,
reviewed in full — 3,844 lines across 30 files). This doc reconciles the
external ChatGPT audit with our own line-level review and our F-findings, and
issues a **keep / rebuild / kill** verdict per module under D1's
redesign-with-reuse rule.

## Headline

The external audit's five critical claims are **all confirmed at line level**,
plus we found three more it missed. Nothing in v2 should run with real money;
most of it should never run at all. What survives is *role* value, not code.

## Confirmed critical defects (external audit → line evidence)

1. **Market data never populated.** `main.py` defines `active_markets: list = []`
   and passes `lambda: active_markets` to herald/macro/link/sentiment — but
   **nothing ever appends to it**. Those four agents run on an empty universe
   forever.
2. **ZEUS records unfilled orders as trades.** `execute_trade` appends a record
   with `status: "submitted"` immediately after `create_and_post_order` and
   never checks fill status; `get_stats` counts those as `trades_executed`.
   AEGIS positions are opened on submission. Comment says "FOK for arb trades"
   but no `OrderType` is ever passed — orders are default GTC-style.
3. **AEGIS risk loop is dead code.** Nothing in the codebase ever calls
   `close_position` (grep across all 30 files: zero call sites), so
   `daily_pnl` can never move, the daily-loss kill switch can never trigger,
   and no position is ever settled or realized. The profit-sweep and
   auto-compound math never executes either.
4. **WATCHDOG does not restart anything.** `check_health` detects
   `task.done()`, logs, alerts, and increments `restart_counts` — but never
   creates a replacement task. Docstring: "Auto-restarts crashed tasks." It
   does not.
5. **Security holes.** FastAPI app has `allow_origins=["*"]` with
   unauthenticated POST `/wallets/add` that accepts a **private key** over the
   network, and POST `/aegis/revive` that clears the kill switch. Private keys
   are held in plain dicts in `WalletManager`.

## Additional defects found in our review (not in the external audit)

6. **MIDAS's spread calculation is meaningless.**
   `spread = abs(yes_p + no_p - 1.0)` on Gamma's normalized `outcomePrices`
   — that sum is ~1.0 by definition, so "spread" is always ~0 and the
   market-making target selector is selecting on noise. Real spread requires
   the CLOB book.
7. **AEGIS theme check is bypassed.** `ZEUS.execute_trade` calls
   `self.aegis.approve_trade(market_id, size_usdc, side)` — the 4th parameter
   `question` is never passed, so every trade is theme "other" and the 15%
   correlated-exposure limit lumps everything into one bucket.
8. **ARGUS fee model is absent.** All three "exploits" ignore the taker fee
   (0.05 × min(p, 1−p)) and the top-of-book spread; F1 measured exactly this
   class of opportunity on live data: **all 9 complete negRisk events were
   negative in both directions** after realistic costs.
9. **`resolution_arb` is H-B1 with no discipline.** Buys any token > 0.95 with
   ≥$3k volume, no cost model, no flip-risk measurement, no pre-registration.
   F3/F4 measured this zone properly: the edge exists (lag=7d OOS PASS) but
   is **size-limited** with a 4.1× higher real half-spread than v2 assumes.
10. **ZEUS blocks the event loop.** `create_and_post_order` is synchronous
    inside `async def`, freezing all agents during order submission.
11. **`get_balance` will throw.** `py_clob_client.ClobClient` has no
    `get_balance()` method; it's wrapped in try/except so the dashboard just
    always shows $0.

## Triage per D1 (10 agents → 3 roles + signal feeds)

| v2 module | Verdict | Rationale |
|---|---|---|
| ARGUS | **KILL the arb logic** | F1: measured negative everywhere at top-of-book. Its "resolution arb" idea is absorbed into H-B1 with proper costs/pre-registration. |
| HERALD (LLM news) | **KEEP as signal feed** | This is the H-LLM hypothesis. Zero authority until ORACLE-style calibration beats market Brier (F2 baseline: 0.0882). The prompt engineering is decent and reusable. |
| ORACLE | **KEEP as role, rebuild body** | Its `track_brier` is the only honest line in v2, but it has no calibration pipeline, no OOS discipline, no comparison vs market. Rebuilt as ORACLE calibration shadow. |
| ZEUS | **REBUILD from scratch** | Executor must be book-aware (walks the book like our `fill_from_book`), fill-confirming, async-native, and journal every state change. Nothing here survives. |
| AEGIS | **KEEP as role, rebuild body** | Right idea (kill switch, daily loss, theme caps, position cap). Rebuild wired to real fills and real settlements. |
| HERALD→ORACLE overlap | **MERGE** | Both are "LLM opinion" sources; D1 gives them one home. |
| PHANTOM (whale watch) | **SIGNAL FEED only** | Copy-trading whales has no evidence either way; treat as data, never as a trigger. |
| GHOST (momentum) | **SIGNAL FEED only** | Prediction-market momentum is unproven here; fine as a feature for ORACLE, never a standalone trigger. |
| MIDAS | **KEEP as role (H-A2), rebuild body** | The maker-zone hypothesis is our F5 zone, but v2's spread math is broken (defect 6) and it never managed inventory risk. |
| MACRO | **KILL** | News→macro→market causal chain with no evidence; redundant with HERALD feed. |
| LINK (cross-market arb) | **KILL** | Same class as ARGUS; F1 evidence applies. |
| GA optimizer | **KILL** | Genetic search over strategy params with no OOS discipline = overfitting machine. QuantEdge killed its equivalent; same DNA rule applies. |
| Sentiment (GDELT) | **SIGNAL FEED only** | Cheap data source; may feed ORACLE later. |
| Telegram/Email reporters | **KEEP concept, port to our Journal** | Alerting is fine; v2's is 400 lines of hand-rolled HTTP we can do in ~80 against our `Journal`. |
| FastAPI/WS dashboard | **DEFER** | Not needed for research platform phase; rebuild minimal when there is something to watch. |
| `deploy.py`, WalletManager | **KILL** | Private-key handling is a liability with zero present need; paper-only phase needs no wallets. |

## What v2 gets right (worth saying)

- Honest Brier tracking intent (ORACLE), kill-switch *concept* (AEGIS), and
  the recognition that the maker/rebate side (MIDAS) is where uncontested
  money might live — D1's three roles are exactly these. The v2 architecture
  failed in wiring and discipline, not in its choice of directions.

## Next actions

1. ORACLE calibration shadow: log LLM-vs-market probability pairs daily; zero
   authority until OOS Brier < market's (F2 baseline 0.0882). Pre-register.
2. H-B1 paper harness: daily scan of ≥0.97 zone, 7d-before-end entries,
   decision-time book snapshots (sharpens F4's cost model into a D2 amendment
   if warranted, labeled per protocol).
3. AEGIS rebuild spec from this doc's confirmed defects list (fills, settles,
   theme exposure passed from caller, UTC day boundary).
