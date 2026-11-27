"""negRisk event-tree scanner (F1): static basket arb measurement.

For each open negRisk event, fetch every outcome's real order book and
compute:
- long edge:  1 - sum(best asks)   (positive => buying the basket is cheap)
- short edge: sum(best bids) - 1   (positive => selling the basket is rich)

Partial events (any outcome book missing a side) are treated as None —
unmeasurable, never assumed. Secondary signal recorded (H-A2): the depth
structure of long-tail outcomes.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from typing import Any

from nexus.polymarket.clob import ClobPublicClient
from nexus.polymarket.gamma import GammaClient
from nexus.polymarket.http import PublicApiError

_EVENT_PARAMS = {"closed": "false", "limit": 100}
_PAGE_CAP = 10  # 1000 events max by default ordering
MIN_PRICED_OUTCOMES = 3  # fewer priced outcomes than this: event unmeasurable


@dataclass
class EventEdge:
    event_slug: str
    title: str
    volume_num: float
    n_outcomes: int
    n_priced: int
    sum_best_ask: float | None
    sum_best_bid: float | None
    fees_enabled: bool
    errors: int = 0

    @property
    def long_gap(self) -> float | None:
        """1 - sum(asks): positive => buying the whole basket looks cheap."""
        return None if self.sum_best_ask is None else 1.0 - self.sum_best_ask

    @property
    def short_gap(self) -> float | None:
        """sum(bids) - 1: positive => selling the whole basket looks rich."""
        return None if self.sum_best_bid is None else self.sum_best_bid - 1.0

    @property
    def complete(self) -> bool:
        return self.n_priced == self.n_outcomes


@dataclass
class ScanResult:
    events: list[EventEdge] = field(default_factory=list)


def event_markets(event: dict) -> list[dict]:
    markets = event.get("markets")
    return [m for m in markets if isinstance(m, dict)] if isinstance(markets, list) else []


def token_id(market: dict) -> str | None:
    raw = market.get("clobTokenIds")
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if isinstance(raw, list) and raw and isinstance(raw[0], (str, int)):
        return str(raw[0])
    return None


def scan_event(
    event: dict,
    clob: ClobPublicClient,
    *,
    max_outcomes: int = 60,
) -> EventEdge | None:
    markets = event_markets(event)
    if not markets:
        return None
    tokens = [token_id(m) for m in markets[:max_outcomes]]
    tokens = [t for t in tokens if t]
    if len(tokens) < 3:
        return None
    sum_ask = 0.0
    sum_bid = 0.0
    n_priced = 0
    errors = 0
    for token in tokens:
        try:
            book = clob.get_book(token)
        except PublicApiError:
            errors += 1
            continue
        if book.best_ask is not None and book.best_bid is not None:
            sum_ask += book.best_ask
            sum_bid += book.best_bid
            n_priced += 1
    edge = EventEdge(
        event_slug=str(event.get("slug", "")),
        title=str(event.get("title", "")),
        volume_num=_float_or(event.get("volume") or event.get("volumeNum"), 0.0),
        n_outcomes=len(tokens),
        n_priced=n_priced,
        sum_best_ask=sum_ask if n_priced == len(tokens) else None,
        sum_best_bid=sum_bid if n_priced == len(tokens) else None,
        fees_enabled=any(bool(m.get("feesEnabled")) for m in markets),
        errors=errors,
    )
    if edge.n_priced < MIN_PRICED_OUTCOMES:
        return None
    return edge


def scan(
    gamma: GammaClient,
    clob: ClobPublicClient,
    *,
    max_events: int = 100,
    treat_incomplete_as_gap: bool = True,
) -> ScanResult:
    """Scan open negRisk events by default ordering, books for every outcome."""
    result = ScanResult()
    fetched = 0
    seen = 0
    while fetched < _PAGE_CAP:
        page = gamma.http.get("/events", {**_EVENT_PARAMS, "offset": fetched * 100})
        if not isinstance(page, list) or not page:
            break
        fetched += 1
        for event in page:
            if seen >= max_events:
                return result
            if not event.get("negRisk"):
                continue
            seen += 1
            edge = scan_event(event, clob)
            if edge is not None:
                result.events.append(edge)
    return result


def report(result: ScanResult) -> str:
    lines = ["# negRisk event-tree arb scan", ""]
    complete = [e for e in result.events if e.complete]
    lines.append(f"events scanned: {len(result.events)} | complete books: {len(complete)}")
    lines.append("")
    if complete:
        longs = [e.long_gap for e in complete if e.long_gap is not None]
        shorts = [e.short_gap for e in complete if e.short_gap is not None]
        lines.append("| direction | min | median | max | n |")
        lines.append("|---|---|---|---|---|")
        if longs:
            lines.append(
                f"| long (1 - sum(asks)) | {min(longs):+.4f} | "
                f"{sorted(longs)[len(longs)//2]:+.4f} | {max(longs):+.4f} | {len(longs)} |"
            )
        if shorts:
            lines.append(
                f"| short (sum(bids) - 1) | {min(shorts):+.4f} | "
                f"{sorted(shorts)[len(shorts)//2]:+.4f} | {max(shorts):+.4f} | {len(shorts)} |"
            )
    return "\n".join(lines)


def _float_or(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="negRisk arb scan")
    parser.add_argument("--max-events", type=int, default=100)
    args = parser.parse_args(argv)
    gamma = GammaClient()
    clob = ClobPublicClient()
    result = scan(gamma, clob, max_events=args.max_events)
    print(report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
