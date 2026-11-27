"""Capacity & spread measurement for the H-B1 zone (D4 protocol).

Cross-sectional snapshot: sample open binary markets, keep those with EITHER
token's midpoint >= 0.97 (D5 amendment: H-B1 buys any token >= 0.97, not
just YES), snapshot both books, measure top-of-book half-spread and realized
slippage for $100/$500 notional taker orders. Protocol and decision rule are
pre-registered in docs/DECISIONS.md (D4/D5) — this module only measures.

    python -m nexus.research.capacity --max-candidates 1000
"""
from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass, field

from nexus.polymarket.clob import ClobPublicClient, FillEstimate, fill_from_book
from nexus.polymarket.gamma import GammaClient, Market

ZONE_MIN = 0.97
NOTIONALS = (100.0, 500.0)


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[idx]


@dataclass
class TokenSnapshot:
    token_id: str
    side: str  # "YES" | "NO"
    best_bid: float | None
    best_ask: float | None
    half_spread: float | None
    slip_100: float | None
    slip_500: float | None
    bid_depth_usd: float
    ask_depth_usd: float


@dataclass
class MarketSnapshot:
    market_id: str
    question: str
    volume_num: float | None
    fees_enabled: bool | None
    tick_size: float | None
    yes_mid: float
    tokens: list[TokenSnapshot] = field(default_factory=list)


def _book_usd_depth(book) -> tuple[float, float]:
    bid_usd = sum(l.price * l.size for l in book.bids)
    ask_usd = sum(l.price * l.size for l in book.asks)
    return bid_usd, ask_usd


def _slip(fill: FillEstimate, mid: float | None) -> float | None:
    if fill.average_price is None or mid is None:
        return None
    return fill.average_price - mid


def snapshot_market(clob: ClobPublicClient, market: Market, yes_mid: float) -> MarketSnapshot | None:
    """Fetch both books for a qualifying market; returns None if unmeasurable."""
    try:
        tokens = json.loads(market.raw["clobTokenIds"]) if market.raw.get("clobTokenIds") else []
    except (ValueError, TypeError):
        tokens = []
    if len(tokens) != 2 or not all(isinstance(t, str) and t for t in tokens):
        return None
    snap = MarketSnapshot(
        market_id=market.id,
        question=market.question,
        volume_num=market.volume_num,
        fees_enabled=market.fees_enabled,
        tick_size=market.tick_size,
        yes_mid=yes_mid,
    )
    for i, token_id in enumerate(tokens):
        try:
            book = clob.get_book(token_id)
        except Exception:
            return None
        if not book.bids or not book.asks:
            return None
        mid = (book.best_bid + book.best_ask) / 2.0
        half = (book.best_ask - book.best_bid) / 2.0
        bid_usd, ask_usd = _book_usd_depth(book)

        def _walk(side: str, usd: float) -> float | None:
            levels = book.asks if side == "buy" else book.bids
            px = levels[0].price
            if px <= 0:
                return None
            shares = usd / px
            try:
                fill = fill_from_book(book, side, shares)
            except Exception:
                return None
            return _slip(fill, mid)

        snap.tokens.append(TokenSnapshot(
            token_id=token_id,
            side="YES" if i == 0 else "NO",
            best_bid=book.best_bid,
            best_ask=book.best_ask,
            half_spread=half,
            slip_100=_walk("buy", 100.0),
            slip_500=_walk("buy", 500.0),
            bid_depth_usd=bid_usd,
            ask_depth_usd=ask_usd,
        ))
    return snap


def sample_candidates(gamma: GammaClient, max_candidates: int) -> list[Market]:
    out: list[Market] = []
    for m in gamma.markets(max_markets=max_candidates, page_size=100,
                           active="true", closed="false", order="volumeNum",
                           ascending="false"):
        if m.is_binary:
            out.append(m)
        if len(out) >= max_candidates:
            break
    return out


def _token_ids(market: Market) -> list[str]:
    try:
        tokens = json.loads(market.raw["clobTokenIds"]) if market.raw.get("clobTokenIds") else []
    except (ValueError, TypeError):
        tokens = []
    return [t for t in tokens if isinstance(t, str) and t]


def run(gamma: GammaClient, clob: ClobPublicClient, max_candidates: int = 1000,
        target_books: int = 60, sleep: float = 0.1) -> dict:
    """Execute the D4 protocol as amended by D5: a market qualifies if EITHER
    token's midpoint >= 0.97 (H-B1 buys any token >= 0.97, not just YES)."""
    import time as _time

    candidates = sample_candidates(gamma, max_candidates)
    print(f"candidates: {len(candidates)} open binary markets (volume desc)")
    snaps: list[MarketSnapshot] = []
    checked = 0
    for m in candidates:
        tokens = _token_ids(m)
        if len(tokens) != 2:
            continue
        try:
            mids = [clob.get_midpoint(t) for t in tokens]
        except Exception:
            continue
        checked += 1
        zone_mid = next((mv for mv in mids if mv is not None and mv >= ZONE_MIN), None)
        if zone_mid is None:
            continue
        snap = snapshot_market(clob, m, zone_mid)
        if snap is not None:
            snaps.append(snap)
            print(f"  zone hit: {snap.market_id} mid={zone_mid:.4f} "
                  f"hs={snap.tokens[0].half_spread:.4f} vol={snap.volume_num}")
        if len(snaps) >= target_books:
            break
        _time.sleep(sleep)
    stats = aggregate(snaps)
    stats["n_candidates_checked"] = checked
    stats["n_candidates_sampled"] = len(candidates)
    return {"snapshots": snaps, "stats": stats}


def aggregate(snaps: list[MarketSnapshot]) -> dict:
    half_spreads: list[float] = []
    slip100: list[float] = []
    slip500: list[float] = []
    bid_depth: list[float] = []
    ask_depth: list[float] = []
    fee_on = 0
    for s in snaps:
        if s.fees_enabled:
            fee_on += 1
        for t in s.tokens:
            if t.half_spread is not None:
                half_spreads.append(t.half_spread)
            if t.slip_100 is not None:
                slip100.append(t.slip_100)
            if t.slip_500 is not None:
                slip500.append(t.slip_500)
            bid_depth.append(t.bid_depth_usd)
            ask_depth.append(t.ask_depth_usd)
    return {
        "n_markets": len(snaps),
        "n_books": len(snaps) * 2,
        "n_fee_enabled": fee_on,
        "half_spread": {"median": _median(half_spreads), "p90": _percentile(half_spreads, 0.9),
                        "max": max(half_spreads) if half_spreads else None},
        "slip_100": {"median": _median(slip100), "p90": _percentile(slip100, 0.9)},
        "slip_500": {"median": _median(slip500), "p90": _percentile(slip500, 0.9)},
        "bid_depth_usd": {"median": _median(bid_depth)},
        "ask_depth_usd": {"median": _median(ask_depth)},
    }


def report(result: dict) -> str:
    st = result["stats"]
    lines = ["# D4 capacity snapshot — H-B1 zone (mid >= 0.97)", ""]
    lines.append(
        f"candidates sampled: {st['n_candidates_sampled']} | checked midpoints: "
        f"{st['n_candidates_checked']} | qualifying books: {st['n_markets']} markets / "
        f"{st['n_books']} token books | fee-enabled: {st['n_fee_enabled']}"
    )
    lines.append("")
    lines.append("| metric | median | p90 | max |")
    lines.append("|---|---|---|---|")

    def fmt(v):
        return f"{v:.5f}" if isinstance(v, float) else "—"

    hs = st["half_spread"]
    lines.append(f"| top-of-book half-spread | {fmt(hs['median'])} | {fmt(hs['p90'])} | {fmt(hs['max'])} |")
    for key, label in (("slip_100", "slippage $100 buy"), ("slip_500", "slippage $500 buy")):
        s = st[key]
        lines.append(f"| {label} | {fmt(s['median'])} | {fmt(s['p90'])} | — |")
    lines.append(f"| bid depth (USD, whole book) | {fmt(st['bid_depth_usd']['median'])} | — | — |")
    lines.append(f"| ask depth (USD, whole book) | {fmt(st['ask_depth_usd']['median'])} | — | — |")
    lines.append("")
    lines.append("comparison line: F3 assumed half-spread = 0.00200 (pre-registered D2).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D4 capacity snapshot")
    parser.add_argument("--max-candidates", type=int, default=1000)
    parser.add_argument("--out", default="data/capacity_snapshot.json")
    parser.add_argument("--report-out", default="data/capacity_report.md")
    args = parser.parse_args(argv)
    gamma = GammaClient()
    clob = ClobPublicClient()
    result = run(gamma, clob, max_candidates=args.max_candidates)
    rep = report(result)
    print(rep)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({
            "stats": result["stats"],
            "snapshots": [vars(s) | {"tokens": [vars(t) for t in s.tokens]} for s in result["snapshots"]],
        }, f, indent=2)
    with open(args.report_out, "w", encoding="utf-8") as f:
        f.write(rep + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
