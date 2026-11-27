# Architecture

Five layers, built strictly in phase order. Nothing at layer N runs until
layer N-1 has earned evidence.

```
┌─────────────────────────────────────────────────────────────────┐
│ 5. EXECUTION (future decision)   real orders — gated, off by    │
│    default; requires wallet L2 creds + explicit operator signoff │
├─────────────────────────────────────────────────────────────────┤
│ 4. AGENT LAYER                  3 roles max (ORACLE/MIDAS/      │
│    AEGIS). LLMs are SIGNAL FEEDS with zero authority until      │
│    their calibration beats the market's (see F2/RESEARCH)       │
├─────────────────────────────────────────────────────────────────┤
│ 3. VALIDATION & PAPER           pre-registered backtests, walk- │
│    forward OOS grading, paper engine (book-walking fills,       │
│    fees, position caps, journal)                                │
├─────────────────────────────────────────────────────────────────┤
│ 2. STRATEGY CORE                hypothesis registry: every      │
│    strategy is a pre-registered hypothesis with kill criteria   │
│    BEFORE data is run on it (D2 pattern)                        │
├─────────────────────────────────────────────────────────────────┤
│ 1. DATA LAYER (built)           Gamma + CLOB + Data API v2      │
│    clients, SQLite research store, collectors, snapshots        │
└─────────────────────────────────────────────────────────────────┘
```

## Design principles

1. **Stdlib-only clients.** `urllib` + gzip; no SDKs, no secret surface.
   Public read endpoints need no auth; trading endpoints are out of scope.
2. **Pre-registration or it didn't happen.** Hypotheses, thresholds, cost
   models, and kill criteria are committed to `docs/DECISIONS.md` BEFORE
   results exist. Retro-editing is forbidden; failed hypotheses die loudly
   in `docs/FINDINGS.md`.
3. **Paper first, always.** The paper engine (`nexus/paper/`) is the only
   sanctioned execution surface. It is deterministic, offline-testable, and
   book-walking by construction.
4. **Measurements ≠ strategies.** Modules like `capacity.py` and
   `dormant.py` record what IS; they make no claims beyond their
   pre-registered decision rules.
5. **One store, reproducible everything.** SQLite at `data/` (gitignored);
   every FINDINGS entry names the command that reproduces it.

## Non-goals (until gates pass)

- Live order submission, wallet handling, private-key custody
- LLM agents with trade authority (ORACLE has zero authority until its
  calibration beats the market's on pre-registered criteria)
- Any GUI/dashboard before the paper phase produces a positive record
