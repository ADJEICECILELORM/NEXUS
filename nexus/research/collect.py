"""Collect resolved (closed) markets and their winner into the local Store.

Phase 1 data collection: paginated Gamma fetch of closed markets ordered by
volume desc; the winner (outcome index whose final price is 1) is derived from
`outcomePrices`. `__main__` runs a collection batch from the repo root:
    python -m nexus.research.collect --max-markets 6000 --page-size 100
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from nexus.polymarket.gamma import GammaClient
from nexus.research.store import Store


def winner_index(outcome_prices: list[float | None]) -> int | None:
    """Final market convention: winner's price is exactly 1 (loser 0)."""
    best: int | None = None
    best_price = 0.0
    for i, price in enumerate(outcome_prices):
        if price is not None and price > best_price:
            best_price = price
            best = i
    if best is not None and best_price >= 0.999:
        return best
    return None


def collect_resolved(
    store: Store,
    gamma: GammaClient,
    *,
    max_markets: int = 1000,
    page_size: int = 100,
) -> int:
    """Fetch closed markets (volume desc) into the store. Returns count added."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    added = 0
    for market in gamma.markets_keyset(
        max_markets=max_markets, page_size=page_size, closed="true"
    ):
        raw = market.raw
        try:
            tokens = json.loads(raw["clobTokenIds"]) if raw.get("clobTokenIds") else []
        except (ValueError, TypeError):
            tokens = []
        events = raw.get("events") or []
        event_slug = events[0].get("slug") if events else None
        outcomes_str = None
        try:
            outcomes = json.loads(raw["outcomes"]) if raw.get("outcomes") else None
            outcomes_str = json.dumps(outcomes) if outcomes else None
        except (ValueError, TypeError):
            outcomes = None
        store.upsert_resolved_market(
            {
                "market_id": market.id,
                "question": market.question,
                "slug": market.slug,
                "condition_id": market.condition_id,
                "event_slug": event_slug,
                "outcomes": outcomes_str,
                "winner_outcome_index": winner_index(market.outcome_prices),
                "clob_token_ids": json.dumps(tokens),
                "volume_num": market.volume_num,
                "liquidity_num": market.liquidity_num,
                "volume_24h": market.volume_24h,
                "neg_risk": market.neg_risk,
                "fees_enabled": market.fees_enabled,
                "end_date": market.end_date,
                "collected_at": now,
            }
        )
        added += 1
    store.commit()
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="collect resolved markets")
    parser.add_argument("--db", default="data/nexus_research.db")
    parser.add_argument("--max-markets", type=int, default=1000)
    parser.add_argument("--page-size", type=int, default=100)
    args = parser.parse_args(argv)
    store = Store(args.db)
    gamma = GammaClient()
    try:
        added = collect_resolved(
            store, gamma, max_markets=args.max_markets, page_size=args.page_size
        )
    finally:
        store.close()
    print(f"collected {added} resolved markets into {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
