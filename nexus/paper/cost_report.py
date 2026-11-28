"""
Cost report: aggregate the H-B1 harness's decision-time book snapshots.

Reads the harness journal (data/hb1_paper.jsonl by default), selects every
`hb1_decision` record, and reports the accumulated distribution of real
executable costs in the H-B1 zone: half-spreads, $100 slippage, ask depth,
by day and overall. This is the dataset that either confirms or amends the
D2 half-spread assumption (amendment must be labeled, per DECISIONS).

Usage:
    python -m nexus.paper.cost_report --journal data/hb1_paper.jsonl
    python -m nexus.paper.cost_report --markdown > docs/COSTS.md
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def load_decisions(journal_path: str | Path) -> list[dict]:
    path = Path(journal_path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") == "hb1_decision":
            out.append(rec)
    return out


def _stats(values: list[float]) -> dict | None:
    if not values:
        return None
    sv = sorted(values)
    return {
        "n": len(sv),
        "min": round(sv[0], 6),
        "median": round(statistics.median(sv), 6),
        "mean": round(statistics.fmean(sv), 6),
        "max": round(sv[-1], 6),
    }


def aggregate(decisions: list[dict]) -> dict:
    """Aggregate decision snapshots overall and per UTC day."""
    days: dict[str, list[dict]] = defaultdict(list)
    for rec in decisions:
        days[str(rec.get("ts", ""))[:10]].append(rec)

    def day_stats(recs: list[dict]) -> dict:
        hs = [r["half_spread"] for r in recs if r.get("half_spread") is not None]
        slip = [r["slippage_100"] for r in recs
                if r.get("slippage_100") is not None]
        depth = [r["depth_usd"] for r in recs if r.get("depth_usd") is not None]
        return {
            "decisions": len(recs),
            "entered": sum(1 for r in recs if r.get("outcome") == "filled"),
            "rejected": sum(1 for r in recs if r.get("outcome") == "rejected"),
            "half_spread": _stats(hs),
            "slippage_100": _stats(slip),
            "depth_usd": _stats(depth),
        }

    overall = day_stats(decisions)
    by_day = {day: day_stats(recs) for day, recs in sorted(days.items())}
    return {"overall": overall, "by_day": by_day}


D2_ASSUMED_HALF_SPREAD = 0.002  # pre-registered D2 assumption, for context


def markdown_report(agg: dict) -> str:
    lines = [
        "# H-B1 zone: measured executable costs",
        "",
        "Source: decision-time book snapshots from the paper harness journal",
        "(`nexus.paper.hb1_harness`). Costs the D2 backtest assumed vs. costs",
        "the market actually quotes. Updated as the harness accumulates days.",
        "",
        f"D2 assumed half-spread: **{D2_ASSUMED_HALF_SPREAD}** (pre-registered).",
        "",
        "## Overall",
        "",
        "| metric | n | min | median | mean | max |",
        "|---|---|---|---|---|---|",
    ]
    ov = agg["overall"]
    for key, label in (("half_spread", "half-spread"),
                       ("slippage_100", "$100 slippage"),
                       ("depth_usd", "ask depth (USD)")):
        s = ov.get(key)
        if s:
            lines.append(
                f"| {label} | {s['n']} | {s['min']} | {s['median']} | "
                f"{s['mean']} | {s['max']} |"
            )
    lines += [
        "",
        f"Decisions: {ov['decisions']} | entered: {ov['entered']} | "
        f"rejected: {ov['rejected']}",
        "",
        "## By day",
        "",
        "| day | decisions | median half-spread | median $100 slip | median depth |",
        "|---|---|---|---|---|",
    ]
    for day, st in agg["by_day"].items():
        hs = st["half_spread"]["median"] if st["half_spread"] else "-"
        slip = st["slippage_100"]["median"] if st["slippage_100"] else "-"
        depth = st["depth_usd"]["median"] if st["depth_usd"] else "-"
        lines.append(f"| {day} | {st['decisions']} | {hs} | {slip} | {depth} |")

    hs = ov.get("half_spread")
    if hs:
        ratio = hs["median"] / D2_ASSUMED_HALF_SPREAD
        lines += [
            "",
            f"Median half-spread is **{ratio:.1f}x** the D2 assumption. "
            "If this persists as n grows, a labeled D-amendment re-grades F3 "
            "at measured costs per the D4/D5 pattern - no claims move until "
            "then.",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate H-B1 decision-time cost snapshots",
    )
    parser.add_argument("--journal", default="data/hb1_paper.jsonl")
    parser.add_argument("--markdown", action="store_true",
                        help="emit markdown instead of JSON")
    args = parser.parse_args()

    agg = aggregate(load_decisions(args.journal))
    if args.markdown:
        print(markdown_report(agg))
    else:
        print(json.dumps(agg, indent=1))


if __name__ == "__main__":
    main()
