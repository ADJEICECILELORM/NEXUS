"""ORACLE scorecard — D6 protocol mechanics.

Forecast pass: scan the same daily window as the H-B1 harness, ask the LLM
for an unanchored P(YES), capture the market midpoint as baseline, journal
`oracle_forecast` records. Score pass: when markets resolve, journal
`oracle_settlement` records with paired Brier scores. Verdict per D6:
PASS / NO EDGE / INSUFFICIENT, computed on the last forecast per market.

Zero authority: nothing here trades, sizes, or gates anything.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from nexus.journal import Journal
from nexus.oracle.llm import AnthropicClient, LlmClient, LlmError, DEFAULT_MODEL
from nexus.paper.aegis import theme_of
from nexus.paper.hb1_harness import LAG_TOLERANCE_DAYS, PRIMARY_LAG_DAYS, parse_end_date
from nexus.polymarket.clob import ClobPublicClient
from nexus.polymarket.gamma import GammaClient, Market

FORECAST_WINDOW_DAYS = 1.0          # dedupe: one forecast per market per UTC day
MAX_FORECASTS_PER_DAY = 30          # D6 cost guard
N_MIN_MARKETS = 100                 # D6 pass criterion (a)
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261128


# --------------------------------------------------------------------------
# Forecast pass


def window_bounds(now: datetime) -> tuple[str, str]:
    """Same end_date window as the H-B1 harness (now+6d .. now+9d)."""
    lo = (now + timedelta(days=PRIMARY_LAG_DAYS - LAG_TOLERANCE_DAYS)).strftime("%Y-%m-%d")
    hi = (now + timedelta(days=PRIMARY_LAG_DAYS + LAG_TOLERANCE_DAYS + 1)).strftime("%Y-%m-%d")
    return lo, hi


def theme_class(question: str) -> str:
    """D6 split: sports vs non-sports (theme_of buckets carry a 'sports' key)."""
    return "sports" if theme_of(question) == "sports" else "non-sports"


def run_forecasts(
    gamma: GammaClient,
    clob: ClobPublicClient,
    llm: LlmClient,
    journal: Journal,
    *,
    model: str = DEFAULT_MODEL,
    now: Optional[datetime] = None,
    max_forecasts: int = MAX_FORECASTS_PER_DAY,
) -> dict:
    """One daily pass: forecast every in-window market not yet done today."""
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    already = {
        rec.get("market_id")
        for rec in journal.read()
        if rec.get("type") == "oracle_forecast"
        and rec.get("day") == today
    }

    lo, hi = window_bounds(now)
    summary = {"scanned": 0, "in_window": 0, "forecast": 0, "skipped_today": 0,
               "llm_error": 0, "no_book": 0}

    for market in gamma.markets_keyset(
        max_markets=500, page_size=100, active="true", closed="false",
        end_date_min=lo, end_date_max=hi, order="endDate", ascending="true",
    ):
        if summary["forecast"] >= max_forecasts:
            break
        summary["scanned"] += 1
        end = parse_end_date(market)
        if end is None or abs((end - now).total_seconds() / 86400.0 - PRIMARY_LAG_DAYS) > LAG_TOLERANCE_DAYS:
            continue
        if not market.clob_token_ids:
            continue
        summary["in_window"] += 1
        if market.id in already:
            summary["skipped_today"] += 1
            continue

        yes_token = market.clob_token_ids[0]
        try:
            market_prob = clob.get_midpoint(yes_token)
        except Exception:
            market_prob = None
        if market_prob is None:
            summary["no_book"] += 1
            continue

        rules = str(market.raw.get("description") or "")
        try:
            llm_prob = float(llm.forecast(market.question, rules))
        except (LlmError, ValueError) as exc:
            summary["llm_error"] += 1
            journal.append({
                "type": "oracle_forecast_error", "market_id": market.id,
                "question": market.question, "error": str(exc)[:300], "day": today,
            })
            continue

        journal.append({
            "type": "oracle_forecast",
            "day": today,
            "market_id": market.id,
            "condition_id": market.condition_id,
            "question": market.question,
            "yes_token_id": yes_token,
            "end_date": market.end_date,
            "llm_prob": round(llm_prob, 6),
            "market_prob": round(float(market_prob), 6),
            "theme_class": theme_class(market.question),
            "model": model,
        })
        summary["forecast"] += 1
    return summary


# --------------------------------------------------------------------------
# Scoring


@dataclass
class SettledPair:
    market_id: str
    question: str
    llm_prob: float
    market_prob: float
    outcome: float
    brier_llm: float
    brier_market: float
    theme_class: str
    model: str


def _last_forecast_per_market(records: Iterable[dict]) -> dict[str, dict]:
    """D6 primary set: the most recent forecast for each market."""
    latest: dict[str, dict] = {}
    for rec in records:
        if rec.get("type") != "oracle_forecast":
            continue
        mid = rec.get("market_id")
        prev = latest.get(mid)
        if prev is None or str(rec.get("ts", "")) > str(prev.get("ts", "")):
            latest[mid] = rec
    return latest


def score_settled(
    gamma: GammaClient,
    journal: Journal,
) -> dict:
    """Score every last-forecast whose market has resolved; journal results."""
    from nexus.paper.settlement import resolution_from_market

    latest = _last_forecast_per_market(journal.read())
    settled_ids = {
        rec.get("market_id")
        for rec in journal.read()
        if rec.get("type") == "oracle_settlement"
    }

    summary = {"considered": len(latest), "already_scored": 0, "unresolved": 0,
               "lookup_failed": 0, "scored": 0}
    for mid, rec in latest.items():
        if mid in settled_ids:
            summary["already_scored"] += 1
            continue
        try:
            found = gamma.list_markets(condition_ids=rec["condition_id"], limit=5)
        except Exception:
            summary["lookup_failed"] += 1
            continue
        market: Market | None = next(
            (Market.from_raw(raw) for raw in found
             if str(raw.get("conditionId", "")) == rec["condition_id"]),
            None,
        )
        if market is None:
            summary["lookup_failed"] += 1
            continue
        resolution = resolution_from_market(market)
        if resolution is None:
            summary["unresolved"] += 1
            continue
        outcome = resolution.get(rec["yes_token_id"])
        if outcome is None:
            summary["unresolved"] += 1
            continue

        b_llm = (rec["llm_prob"] - outcome) ** 2
        b_mkt = (rec["market_prob"] - outcome) ** 2
        journal.append({
            "type": "oracle_settlement",
            "market_id": mid,
            "question": rec.get("question"),
            "llm_prob": rec["llm_prob"],
            "market_prob": rec["market_prob"],
            "outcome": outcome,
            "brier_llm": round(b_llm, 6),
            "brier_market": round(b_mkt, 6),
            "theme_class": rec.get("theme_class", "unknown"),
            "model": rec.get("model", "unknown"),
        })
        summary["scored"] += 1
    return summary


def load_pairs(journal: Journal) -> list[SettledPair]:
    pairs = []
    for rec in journal.read():
        if rec.get("type") != "oracle_settlement":
            continue
        pairs.append(SettledPair(
            market_id=rec["market_id"], question=rec.get("question", ""),
            llm_prob=rec["llm_prob"], market_prob=rec["market_prob"],
            outcome=rec["outcome"], brier_llm=rec["brier_llm"],
            brier_market=rec["brier_market"],
            theme_class=rec.get("theme_class", "unknown"),
            model=rec.get("model", "unknown"),
        ))
    return pairs


# --------------------------------------------------------------------------
# D6 verdict


def _bootstrap_ci(values: Sequence[float], resamples: int, seed: int) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    return means[int(0.025 * resamples)], means[int(0.975 * resamples)]


def verdict_for(pairs: Sequence[SettledPair], *, n_min: int = N_MIN_MARKETS) -> dict:
    """Mechanical D6 verdict for one set of settled pairs."""
    n = len(pairs)
    if n < n_min:
        return {"verdict": "INSUFFICIENT", "n": n, "n_min": n_min}
    diffs = [p.brier_llm - p.brier_market for p in pairs]
    mean_d = sum(diffs) / n
    lo, hi = _bootstrap_ci(diffs, BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED)
    passed = mean_d < 0 and hi < 0
    return {
        "verdict": "PASS" if passed else "NO EDGE",
        "n": n,
        "mean_brier_llm": round(sum(p.brier_llm for p in pairs) / n, 6),
        "mean_brier_market": round(sum(p.brier_market for p in pairs) / n, 6),
        "mean_diff": round(mean_d, 6),
        "ci95_lo": round(lo, 6),
        "ci95_hi": round(hi, 6),
        "n_min": n_min,
    }


def full_verdict(pairs: Sequence[SettledPair]) -> dict:
    """Overall verdict plus the D6-required non-sports split."""
    non_sports = [p for p in pairs if p.theme_class == "non-sports"]
    return {
        "overall": verdict_for(pairs),
        "non_sports": verdict_for(non_sports),
        "sports_n": sum(1 for p in pairs if p.theme_class == "sports"),
    }


def markdown_report(pairs: Sequence[SettledPair]) -> str:
    v = full_verdict(pairs)
    lines = [
        "# ORACLE scorecard (D6)",
        "",
        "Unanchored LLM forecasts vs market midpoints, paired Brier on settled",
        "markets (last forecast per market). Zero authority per D1/D6.",
        "",
        "| set | n | Brier LLM | Brier market | mean diff | 95% CI | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, res in (("overall", v["overall"]), ("non-sports", v["non_sports"])):
        if res["verdict"] == "INSUFFICIENT":
            lines.append(f"| {name} | {res['n']} | - | - | - | - | "
                         f"INSUFFICIENT (n < {res['n_min']}) |")
        else:
            lines.append(
                f"| {name} | {res['n']} | {res['mean_brier_llm']:.4f} | "
                f"{res['mean_brier_market']:.4f} | {res['mean_diff']:+.4f} | "
                f"[{res['ci95_lo']:+.4f}, {res['ci95_hi']:+.4f}] | {res['verdict']} |"
            )
    lines += ["", f"Sports settled markets (report only): {v['sports_n']}", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI


def main() -> None:
    parser = argparse.ArgumentParser(description="ORACLE scorecard (D6)")
    parser.add_argument("command", choices=["forecast", "score", "report"])
    parser.add_argument("--journal", default="data/oracle.jsonl")
    parser.add_argument("--gamma-journal", default="data/hb1_paper.jsonl",
                        help="unused; kept for symmetry")
    parser.add_argument("--api-key-env", default="ANTHROPIC_API_KEY")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-forecasts", type=int, default=MAX_FORECASTS_PER_DAY)
    args = parser.parse_args()

    if args.command == "forecast":
        api_key = args.api_key or os.environ.get(args.api_key_env, "")
        if not api_key:
            print("ORACLE: no API key found — skipping forecast pass (D6 cost guard)")
            return
        gamma = GammaClient()
        clob = ClobPublicClient()
        llm = AnthropicClient(api_key=api_key, model=args.model)
        summary = run_forecasts(
            gamma, clob, llm, Journal(args.journal),
            model=args.model, max_forecasts=args.max_forecasts,
        )
        print(json.dumps(summary, indent=1))
    elif args.command == "score":
        summary = score_settled(GammaClient(), Journal(args.journal))
        print(json.dumps(summary, indent=1))
    else:
        print(markdown_report(load_pairs(Journal(args.journal))))


if __name__ == "__main__":
    main()
