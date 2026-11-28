"""
H-B1 paper harness.

Daily job: scan open binary markets for the H-B1 zone (any token book
midpoint >= 0.97), record a decision-time order-book snapshot (real spread,
depth, slippage), and paper-enter candidates whose end_date is ~7 days out
(D2 primary lag) through the PaperEngine with a real book-walk fill.

This is the paper-shadow counterpart of the F3/F4 backtest: it produces
(a) a paper PnL track record under pre-registered D2 economics, and
(b) a growing dataset of REAL entry costs to either confirm or amend the
    D2 half-spread assumption (amendments must be labeled, per DECISIONS).

Paper only. No orders are ever placed.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from nexus.journal import Journal
from nexus.polymarket.clob import Book, ClobPublicClient, fill_from_book
from nexus.polymarket.gamma import GammaClient, Market
from nexus.paper.aegis import Aegis, AegisConfig
from nexus.paper.engine import PaperConfig, PaperEngine
from nexus.paper.state import load_account, save_account

# D2 pre-registered economics (must match nexus.research.hb1_backtest).
ZONE_THRESHOLD = 0.97
PRIMARY_LAG_DAYS = 7
LAG_TOLERANCE_DAYS = 1.0       # daily scan -> accept end_date within +/- tol
SLIPPAGE_CHECK_USD = 100.0     # record slippage for a $100 market order
MAX_ENTRY_USD = 200.0          # F4: size-limited viability


@dataclass
class Candidate:
    market_id: str
    question: str
    token_id: str
    side: str                 # "YES" | "NO"
    price: float              # book midpoint used for the decision
    end_date: str
    half_spread: float
    depth_usd: float
    slippage_100: float | None

    def to_record(self) -> dict:
        return {
            "market_id": self.market_id,
            "question": self.question,
            "token_id": self.token_id,
            "side": self.side,
            "price": round(self.price, 6),
            "end_date": self.end_date,
            "half_spread": round(self.half_spread, 6),
            "depth_usd": round(self.depth_usd, 2),
            "slippage_100": (
                round(self.slippage_100, 6)
                if self.slippage_100 is not None else None
            ),
        }


def parse_end_date(market: Market) -> Optional[datetime]:
    raw = market.end_date
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def half_spread_of(book: Book) -> Optional[float]:
    if book.best_bid is None or book.best_ask is None:
        return None
    return (book.best_ask - book.best_bid) / 2.0


def ask_depth_usd(book: Book) -> float:
    """Total USD depth on the ask side (the side a buyer lifts)."""
    return sum(level.price * level.size for level in book.asks)


def evaluate_market(
    market: Market,
    books: dict[str, Book],
    now: datetime,
    zone: float = ZONE_THRESHOLD,
    lag_days: float = PRIMARY_LAG_DAYS,
    lag_tol: float = LAG_TOLERANCE_DAYS,
) -> Optional[Candidate]:
    """
    H-B1 selection under D2 economics: binary market, ~PRIMARY_LAG_DAYS from
    end_date, and EITHER token's book midpoint >= zone. The qualifying token
    (the one >= zone) is the entry side.
    """
    if not market.is_binary:
        return None
    end = parse_end_date(market)
    if end is None:
        return None
    days_out = (end - now).total_seconds() / 86400.0
    if abs(days_out - lag_days) > lag_tol:
        return None

    yes_id = market.yes_token_id
    no_id = market.clob_token_ids[1] if len(market.clob_token_ids) > 1 else None

    for token_id, side in ((yes_id, "YES"), (no_id, "NO")):
        if not token_id:
            continue
        book = books.get(token_id)
        if book is None:
            continue
        mid = book.mid
        if mid is None or mid < zone:
            continue
        hs = half_spread_of(book)
        if hs is None:
            continue
        est = fill_from_book(book, "buy", SLIPPAGE_CHECK_USD)
        slip = (est.average_price - mid) if est.fully_filled else None
        return Candidate(
            market_id=market.condition_id or market.id,
            question=market.question,
            token_id=token_id,
            side=side,
            price=mid,
            end_date=str(market.end_date),
            half_spread=hs,
            depth_usd=ask_depth_usd(book),
            slippage_100=slip,
        )
    return None


def run(
    gamma: GammaClient,
    clob: ClobPublicClient,
    engine: PaperEngine,
    journal: Journal,
    *,
    aegis: Aegis | None = None,
    now: Optional[datetime] = None,
    max_markets: int = 300,
    entry_usd: float = MAX_ENTRY_USD,
) -> dict:
    """
    One daily pass: scan -> snapshot books -> paper-enter qualifying
    candidates -> journal decisions. Returns a summary dict.
    """
    now = now or datetime.now(timezone.utc)
    summary: dict = {
        "scanned": 0,
        "in_window": 0,
        "candidates": 0,
        "entered": 0,
        "rejected": 0,
        "snapshot_half_spreads": [],
        "snapshot_slippage_100": [],
    }

    # Target the D2 window directly: the keyset endpoint accepts
    # end_date_min/max (date strings) + order=endDate, so the daily pass
    # only fetches markets plausibly inside the lag window.
    lo = (now + timedelta(days=PRIMARY_LAG_DAYS - LAG_TOLERANCE_DAYS)).strftime("%Y-%m-%d")
    hi = (now + timedelta(days=PRIMARY_LAG_DAYS + LAG_TOLERANCE_DAYS + 1)).strftime("%Y-%m-%d")

    for market in gamma.markets_keyset(
        max_markets=500, page_size=100, active="true", closed="false",
        end_date_min=lo, end_date_max=hi, order="endDate", ascending="true",
    ):
        if summary["scanned"] >= max_markets:
            break
        summary["scanned"] += 1

        end = parse_end_date(market)
        if end is None:
            continue
        days_out = (end - now).total_seconds() / 86400.0
        if abs(days_out - PRIMARY_LAG_DAYS) > LAG_TOLERANCE_DAYS:
            continue
        summary["in_window"] += 1

        tokens = [t for t in market.clob_token_ids if t]
        if not tokens:
            continue
        books: dict[str, Book] = {}
        for token_id in tokens:
            try:
                book = clob.get_book(token_id)
            except Exception:
                book = None
            if book is not None:
                books[token_id] = book

        cand = evaluate_market(market, books, now)
        if cand is None:
            continue
        summary["candidates"] += 1
        summary["snapshot_half_spreads"].append(round(cand.half_spread, 6))
        summary["snapshot_slippage_100"].append(cand.slippage_100)

        # AEGIS pre-trade gate (kill switch, caps, theme exposure).
        if aegis is not None:
            ok, reason = aegis.pre_trade_check(
                cand.token_id, entry_usd, question=cand.question,
                now=now,
            )
            if not ok:
                summary["aegis_blocked"] = summary.get("aegis_blocked", 0) + 1
                journal.append({**cand.to_record(), "type": "hb1_decision",
                                "outcome": "aegis_blocked", "reason": reason,
                                "entry_usd": entry_usd})
                continue

        book = books[cand.token_id]
        fill = engine.buy(
            token_id=cand.token_id,
            market_id=cand.market_id,
            notional_usd=entry_usd,
            book=book,
            reason="hb1_paper",
        )
        outcome = "filled" if fill is not None else "rejected"
        if fill is not None:
            summary["entered"] += 1
            if aegis is not None:
                aegis.apply_fill(cand.token_id, entry_usd, question=cand.question)
        else:
            summary["rejected"] += 1
        journal.append({**cand.to_record(), "type": "hb1_decision",
                        "outcome": outcome, "entry_usd": entry_usd})

    hss = sorted(summary["snapshot_half_spreads"])
    if hss:
        summary["median_half_spread"] = hss[len(hss) // 2]
        summary["mean_half_spread"] = round(sum(hss) / len(hss), 6)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="H-B1 paper harness (daily pass)")
    parser.add_argument("--journal", default="data/hb1_paper.jsonl")
    parser.add_argument("--account", default="data/hb1_account.json")
    parser.add_argument("--max-markets", type=int, default=300)
    parser.add_argument("--entry-usd", type=float, default=MAX_ENTRY_USD)
    parser.add_argument("--aegis", default="data/hb1_aegis.json")
    args = parser.parse_args()

    gamma = GammaClient()
    clob = ClobPublicClient()
    cfg = PaperConfig()
    engine = PaperEngine(cfg, Journal(args.journal))
    engine.account = load_account(args.account, cfg)
    journal = Journal(args.journal)
    aegis = Aegis.load(engine.account, args.aegis, AegisConfig(), journal)

    summary = run(
        gamma, clob, engine, journal,
        aegis=aegis,
        max_markets=args.max_markets, entry_usd=args.entry_usd,
    )
    save_account(engine.account, args.account)
    aegis.save(args.aegis)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
