"""Backfill price history for resolved markets into the Store.

Fetches /v2/prices-history (interval=max) for each outcome token that has no
points yet (unless --force), inserting points in bulk. Rate-limit friendly:
0.15s sleep between fetches.

    python -m nexus.research.backfill --limit 3000
"""
from __future__ import annotations

import argparse
import time

from nexus.research.dataapi import DataApiClient
from nexus.research.store import Store


def backfill(
    store: Store,
    dataapi: DataApiClient,
    *,
    limit: int = 1000,
    force: bool = False,
    sleep: float = 0.15,
) -> dict[str, int]:
    targets = store.all_token_ids()
    stats = {"fetched": 0, "ok": 0, "empty": 0, "failed": 0, "skipped": 0}
    done = 0
    for token_id, _is_winner in targets:
        if done >= limit:
            break
        if not force and store.n_points(token_id) > 0:
            stats["skipped"] += 1
            continue
        done += 1
        try:
            history = dataapi.prices_history(token_id, interval="max")
        except Exception:
            stats["failed"] += 1
            continue
        for point in history.points:
            store.record_price_point(token_id, point.ts, point.price)
        if history.points:
            stats["ok"] += 1
        else:
            stats["empty"] += 1
        stats["fetched"] += 1
        if stats["fetched"] % 25 == 0:
            store.commit()
            print(f"  {stats['fetched']}/{limit} fetched (ok={stats['ok']} "
                  f"empty={stats['empty']} failed={stats['failed']})")
        time.sleep(sleep)
    store.commit()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="backfill price history")
    parser.add_argument("--db", default="data/nexus_research.db")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    store = Store(args.db)
    dataapi = DataApiClient()
    try:
        stats = backfill(store, dataapi, limit=args.limit, force=args.force)
    finally:
        store.close()
    print(f"backfill done: ok={stats['ok']} empty={stats['empty']} "
          f"failed={stats['failed']} skipped={stats['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
