# NEXUS — Decision Log

Append-only. Decisions are written **before** the work they govern wherever the
work could be biased by knowing the results (hypotheses, protocols, kill
criteria). Retroactive amendments are allowed only when clearly labeled
`POST-HOC` and committed before the amended run executes. Never edit an
existing decision in place.

---

## D1 — Project direction (2026-11-26)

**Context.** NEXUS v2 (the operator's prior build, lost to hardware failure and
reviewed via `nexus_v2_final.zip` + external audit) was a 10-agent system
(ARGUS, ORACLE, ZEUS, AEGIS, HERALD, PHANTOM, GHOST, MIDAS, MACRO, LINK) with
17 strategies, a FastAPI service layer, and several critical defects: market
data never populated, AEGIS risk logic incomplete, ZEUS recorded unfilled
orders as positions, WATCHDOG did not actually restart workers, and multiple
security holes.

**Decision.**

1. **Both objectives stand, in this order:** (a) a *research platform* that
   measures what actually works on Polymarket with pre-registered tests, and
   (b) a *trading system* that only ever runs what survived (a). Everything is
   **paper-only** until a hypothesis clears its pre-registered gate and a
   capacity check.
2. **Redesign with reuse, not resurrection.** The v2 agent names are retained
   only as *role* labels; the code is not ported. Consolidation:
   - **ORACLE** — calibration/forecasting (H-LLM). Zero authority until its
     Brier score beats the market's on out-of-sample data.
   - **MIDAS** — the maker/liquidity zone (H-A2), the only zone where live
     evidence suggests an open edge.
   - **AEGIS** — risk and position control, rebuilt from scratch (the v2
     version was incomplete).
   The other seven v2 agents become **signal feeds** or are dropped. Nothing
   trades on an agent's opinion; agents may only consume vetted signals.
3. **Unified plan.** One repo, one journal, one findings ledger. Evidence is
   recorded even when it kills an idea — a loud KILL is a deliverable.

**Consequence.** Phase order: data layer → measurement → pre-registered
backtests → paper engine → (only then) anything live-adjacent.

---

## D2 — H-B1 pre-registration (2026-11-26, BEFORE any backtest run)

**Hypothesis (H-B1).** On Polymarket markets whose price is already ≥ 0.97 for
an outcome, that outcome's price *carries* — the market rarely flips to the
other side, and buying the leading token near 0.97+ and holding to resolution
has positive expected PnL per share after costs.

**Pre-registered parameters (fixed before any execution):**

| Parameter | Value |
|---|---|
| Primary decision lag | 7 days before market end_date |
| Entry zone | token price ≥ 0.97 |
| Fill cost | price + 0.002 half-spread (top-of-book assumption) |
| Fee | taker, 0.05 × min(p, 1−p) per share |
| Split | in-sample 70% / out-of-sample 30%, by market end_date |
| OOS minimum trades | 30 |

**Kill / pass criteria (applied mechanically, in order):**

1. OOS trades **< 30** → verdict **INSUFFICIENT** (no claim either way).
2. OOS mean PnL per share **≤ 0** → **KILL**.
3. OOS flip rate **≥ 2%** → **KILL**.
4. OOS mean > 0 **and** Wilson 95% lower bound on the mean > 0 → **PASS**.
5. Otherwise → **FAIL** (positive but not significant).

**Commitment.** This document was committed before the backtest was run. No
parameter above may be changed after seeing results; a cost-model revision is
permitted only through the D4 measurement path (measure real books, re-grade
with the measured median, label the amendment).

---

## D3 — H-B1 statistical power extension (2026-11-26)

**Context.** First H-B1 run: OOS n=10 → INSUFFICIENT. Collection extended to
3,000 markets: OOS n=21 → still INSUFFICIENT.

**Decision.** Extend the dataset to 6,000 markets. **All D2 criteria remain
unchanged.** This is a power extension only — no threshold, cost, lag, or split
may move. If OOS n ≥ 30 remains unreachable, the verdict is **INSUFFICIENT**
by rule 1 and H-B1 is parked with no claim in either direction.

---

## D4 — Capacity & spread measurement protocol (2026-11-26, BEFORE any snapshot)

**Purpose.** D2's half-spread of 0.002 was an assumption. F4 must measure real
top-of-book costs on live ≥0.97 markets before any paper-trading claim that
depends on them.

**Sampling plan (fixed before the first snapshot):**

- Universe: open binary markets (`active=true`, `closed=false`), ordered by
  volume descending, up to 300 candidates.
- Qualification: YES-token midpoint ≥ 0.97.
- Target: ≥ 60 qualifying books.
- Per qualifying market: full order books for **both** tokens.

**Metrics per book:**

1. Top-of-book half-spread: `(ask − bid) / 2`.
2. Slippage for a $100 market order (book walk, `fill_from_book`).
3. Slippage for a $500 market order.
4. Fee context: taker fee = 0.05 × min(p, 1−p).
5. Comparison line: the D2 assumed half-spread of 0.002.

**Decision rule (mechanical):**

- Median half-spread ≤ 0.002 **and** median $100 slippage ≤ 0.002 → the D2
  cost assumption is **validated**; F3's PASS stands unchanged.
- Median half-spread > 0.002 → **suspend** F3's PASS and re-grade the same D2
  backtest with the **measured median** half-spread substituted. The D2
  pass/fail thresholds do not move; only the cost input does.
- Fewer than 30 qualifying books → **INSUFFICIENT**, which is a decisive
  negative for near-term deployability of H-B1.

---

## D5 — D4 protocol amendment (2026-11-26, **POST-HOC — labeled**)

**What happened.** The first D4 run returned **INSUFFICIENT** by its own rule:
only 2 qualifying books out of 300 candidates, far below the 30-book floor.

**Root cause.** The protocol checked only the **YES** token's midpoint, but
H-B1 (and reality) buys **any** token priced ≥ 0.97 — including NO tokens on
markets where YES is cheap. The protocol excluded half the zone by
construction.

**Amendments (both committed BEFORE the amended re-run):**

1. Zone qualification checks **both** tokens' midpoints: a market qualifies if
   either token's midpoint ≥ 0.97.
2. Candidate pool widened 300 → 1,000.

**Unchanged.** All metrics and the decision rule are exactly as in D4.

**Status.** Labeled POST-HOC per the header rule. The amendment fixes a
specification error in the sampling frame, not an inconvenient result; the
first run's INSUFFICIENT outcome is preserved in FINDINGS F4 for the record.

---

## D6 — ORACLE scorecard protocol (2026-11-28, pre-registered BEFORE any forecast)

**Question.** Does an LLM hold independent predictive signal about target-zone
Polymarket markets, beyond what the market price already encodes?

**Evidence base.** F2: the market is well-calibrated (Brier 0.0882 on 420
decided observations). poly-agent THESIS.md: LLM agents agree with the market
within 5% on 96% of trials and lose money from Kelly on neutral calibration on
$1M+ markets; the hypothesized alpha zone is $10K–$500K non-sports. No public
project publishes a pre-registered LLM-vs-market scorecard (RESEARCH.md gap
analysis).

**Design — one forecast arm, deliberately unanchored.**

- **Population.** Markets in the same daily window the H-B1 harness scans:
  `end_date` in [now+6d, now+9d], active, not closed. Both sports and
  non-sports are collected, but scoring is reported **split by theme class**
  (sports vs non-sports) so the zone claim is testable post hoc without
  cherry-picking.
- **Arm A only (unanchored).** The LLM sees the question and the resolution
  rules and is asked for `P(YES)`. It is **not** shown the market price. This
  measures independent signal. A price-anchored arm would mostly re-emit the
  price (equal Brier, zero information) and is explicitly deferred; adding it
  later requires a labeled POST-HOC amendment.
- **Model.** One fixed model per phase, recorded with each forecast. Changing
  the model starts a new phase; scores never pool across models.
- **Cadence.** One forecast per market per day while it remains in window
  (so each market contributes up to 4 forecasts at lags ~9/8/7/6d). The
  primary scoring set uses **only the last forecast per market** (lag ~6-7d),
  matching the H-B1 decision lag; all forecasts are stored.
- **Baseline.** The market midpoint of the same token, captured at the same
  timestamp as the forecast.

**Scoring (mechanical).**

1. Brier per settled forecast: `b = (p − outcome)^2`, outcome ∈ {0,1} from
   `resolution_from_market` (1.0/0.0 only; anything else leaves the forecast
   unsettled).
2. Paired difference per settled market: `d = brier_llm − brier_market`
   (last-forecast-per-market set). Primary statistic: mean(d).
3. **PASS** requires ALL of: (a) ≥ 100 settled markets in the primary set;
   (b) mean(d) < 0; (c) bootstrap 95% CI of mean(d) (10,000 resamples) lies
   entirely below 0 — separately for the non-sports split and overall.
4. **NO EDGE** = ≥ 100 settled and (b) or (c) fails. **INSUFFICIENT** = < 100
   settled. Neither grants authority.

**Authority rule (unchanged from D1).** ORACLE has **zero authority** during
measurement: no sizing, no gating of any trade, no paper trades on its output.
Only a D6 PASS (non-sports split) promotes ORACLE to a paper-trading role, and
that promotion itself gets a new pre-registered decision before it acts.

**Cost guard.** ≤ 30 forecasts/day, one API call each. If the key is absent
the daily chain skips the step silently; nothing else is blocked on it.

**Falsifiability.** This decision is committed before the first forecast is
made. If ORACLE is later described anywhere as "validated" without a D6 PASS
line in FINDINGS, that claim is void per the header rule.
