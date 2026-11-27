# NEXUS

Evidence-first Polymarket research and paper-trading platform.

**Status: research platform, paper-only.** No order submission, no wallet
credentials, no deployment. Every strategy claim must earn a pre-registered,
out-of-sample verdict in [`docs/FINDINGS.md`](docs/FINDINGS.md) first.

## Why this design

v2 of this project (lost) had 10 agents and 17 strategies before a single
one was validated. The rebuild inverts that: **evidence before agents**.
External evidence (poly-agent's published study) shows LLM agents agree with
the market within 5% on 96% of trials — no demonstrated edge on liquid
markets. So NEXUS starts as a measurement instrument.

## Current evidence ledger (docs/FINDINGS.md)

| id | hypothesis | verdict |
|---|---|---|
| F1 | static negRisk basket arb | KILLED (all 9 complete events negative both directions) |
| F2 | market calibration | measured: Brier 0.0882, well-calibrated in 31/35 cells |
| F3/F4 | H-B1 decided-market carry (buy ≥0.97 @ 7d before end) | OOS PASS (n=57, mean +0.0084/share, 0 flips) — cost-audited, size-limited |
| F5 | H-A2 dormant-tail maker zone | measured surface only: 87.5% of long-tail outcomes lack live two-sided books; adverse-selection cost unmeasured |

## Layout

```
nexus/
  polymarket/   # stdlib-only public API clients (Gamma, CLOB, gzip, retries)
  research/     # store, collectors, calibration, backtests, measurements
  paper/        # deterministic paper-trading engine (book-walking fills)
docs/           # RESEARCH, ARCHITECTURE, ROADMAP, FINDINGS, DECISIONS
tests/          # offline pytest suite (no network in CI)
data/           # local SQLite research store (gitignored)
```

## Development

```bash
python -m pytest tests -q      # 37 tests, offline
python -m nexus.research.collect --max-markets 6000   # live data collection
python -m nexus.research.hb1_backtest                 # pre-registered backtest
python -m nexus.research.capacity                     # D4 capacity snapshot
python -m nexus.research.dormant                      # H-A2 dormant tail
```

Python 3.12, stdlib only. See [docs/ROADMAP.md](docs/ROADMAP.md) for phase
gates and [docs/DECISIONS.md](docs/DECISIONS.md) for the pre-registration log.
