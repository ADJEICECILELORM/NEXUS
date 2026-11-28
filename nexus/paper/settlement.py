"""
Settlement watcher for the H-B1 paper harness.

Daily job: look up every open paper position's market on Gamma; when the
market has closed with final outcome prices (1.0/0.0), settle the position
in the PaperEngine at its true outcome. Positions whose markets are closed
without final prices, or whose lookup fails, are left open and reported.

Paper only. No orders are ever placed.
"""

from __future__ import annotations

import argparse
import json
from typing import Optional

from nexus.journal import Journal
from nexus.polymarket.gamma import GammaClient, Market
from nexus.paper.aegis import Aegis
from nexus.paper.engine import PaperEngine
from nexus.paper.state import save_account

WIN_THRESHOLD = 0.999     # final winner price (matches winner_index in research)
LOSE_THRESHOLD = 0.001    # final loser price


def resolution_from_market(market: Market) -> Optional[dict[str, float]]:
    """
    Final outcome map {token_id: 1.0 | 0.0} for a resolved binary market,
    or None if the market is not closed / prices are not final.
    """
    if not market.closed:
        return None
    prices = market.outcome_prices
    tokens = market.clob_token_ids
    if len(prices) < 2 or len(tokens) < 2:
        return None
    out: dict[str, float] = {}
    for token, price in zip(tokens, prices):
        if price is None:
            return None
        if price >= WIN_THRESHOLD:
            out[token] = 1.0
        elif price <= LOSE_THRESHOLD:
            out[token] = 0.0
        else:
            return None  # unresolved / non-binary final prices
    return out


def settle_pending(
    gamma: GammaClient,
    engine: PaperEngine,
    journal: Journal,
    *,
    aegis: Aegis | None = None,
) -> dict:
    """
    Settle every open position whose market has resolved. Positions sharing
    a market are looked up once. Returns a summary dict.
    """
    positions = dict(engine.account.positions)
    by_market: dict[str, list[str]] = {}
    for token_id, pos in positions.items():
        by_market.setdefault(pos.market_id, []).append(token_id)

    summary: dict = {
        "open_positions": len(positions),
        "markets_checked": 0,
        "still_open": 0,
        "lookup_failed": 0,
        "settled": 0,
        "pnl": 0.0,
    }

    for market_id, token_ids in by_market.items():
        summary["markets_checked"] += 1
        try:
            raws = gamma.list_markets(condition_ids=market_id, limit=5)
        except Exception:
            raws = []
        if not raws:
            summary["lookup_failed"] += 1
            continue
        market = Market.from_raw(raws[0])
        outcomes = resolution_from_market(market)
        if outcomes is None:
            summary["still_open"] += 1
            continue
        for token_id in token_ids:
            price = outcomes.get(token_id)
            if price is None:
                continue
            delta = engine.settle(token_id, price)
            summary["settled"] += 1
            summary["pnl"] += delta
            if aegis is not None:
                aegis.apply_settlement(token_id, market.question, delta)
            journal.append({
                "type": "hb1_settlement",
                "market_id": market_id,
                "token_id": token_id,
                "outcome_price": price,
                "pnl": round(delta, 6),
            })

    summary["pnl"] = round(summary["pnl"], 6)
    summary["cash"] = round(engine.account.cash, 2)
    summary["realized_pnl"] = round(engine.account.realized_pnl, 6)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="H-B1 settlement watcher")
    parser.add_argument("--account", default="data/hb1_account.json")
    parser.add_argument("--journal", default="data/hb1_paper.jsonl")
    parser.add_argument("--aegis", default="data/hb1_aegis.json")
    args = parser.parse_args()

    from nexus.paper.engine import PaperConfig
    from nexus.paper.state import load_account

    journal = Journal(args.journal)
    cfg = PaperConfig()
    engine = PaperEngine(cfg, journal)
    engine.account = load_account(args.account, cfg)
    aegis = Aegis.load(engine.account, args.aegis, journal=journal)

    summary = settle_pending(GammaClient(), engine, journal, aegis=aegis)
    save_account(engine.account, args.account)
    aegis.save(args.aegis)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
