"""Public (read-only) access to the Polymarket CLOB.

Verified live, no auth needed: ``/ok``, ``/book``, ``/price``, ``/midpoint``.
Trading (``POST /order``) requires wallet-derived L2 credentials and is out of
scope here on purpose — see docs/ARCHITECTURE.md phase 4.
"""

from __future__ import annotations

from dataclasses import dataclass

from nexus.polymarket.http import HttpJsonClient, PublicApiError

CLOB_BASE_URL = "https://clob.polymarket.com"


@dataclass
class BookLevel:
    price: float
    size: float


@dataclass
class Book:
    token_id: str
    bids: list[BookLevel]  # sorted best (highest) first
    asks: list[BookLevel]  # sorted best (lowest) first

    @classmethod
    def from_raw(cls, raw: dict, token_id: str) -> "Book":
        bids = _levels(raw.get("bids"), descending=True)
        asks = _levels(raw.get("asks"), descending=False)
        return cls(token_id=token_id, bids=bids, asks=asks)

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.bids and self.asks:
            return (self.best_bid + self.best_ask) / 2.0
        return None


def _levels(raw_levels: object, *, descending: bool) -> list[BookLevel]:
    if not isinstance(raw_levels, list):
        return []
    levels: list[BookLevel] = []
    for entry in raw_levels:
        if not isinstance(entry, dict):
            continue
        try:
            levels.append(BookLevel(price=float(entry["price"]), size=float(entry["size"])))
        except (KeyError, TypeError, ValueError):
            continue
    levels.sort(key=lambda lv: lv.price, reverse=descending)
    return levels


@dataclass
class FillEstimate:
    """Result of walking a book for a target size.

    ``average_price`` is the size-weighted avg fill including only resting
    liquidity actually consumed; ``worst_price`` the worst level touched.
    """

    side: str
    target_size: float
    filled_size: float
    average_price: float | None
    worst_price: float | None

    @property
    def fully_filled(self) -> bool:
        return self.filled_size >= self.target_size - 1e-12


class ClobPublicClient:
    def __init__(self, base_url: str = CLOB_BASE_URL, **http_kwargs: object) -> None:
        self.http = HttpJsonClient(base_url, **http_kwargs)  # type: ignore[arg-type]

    def ok(self) -> bool:
        try:
            text, status = self.http.get_text("/ok")
        except PublicApiError:
            return False
        return status == 200 and bool(text.strip())

    def get_book(self, token_id: str) -> Book:
        data = self.http.get("/book", {"token_id": token_id})
        if not isinstance(data, dict):
            raise PublicApiError(f"CLOB returned non-book payload: {type(data)!r}")
        return Book.from_raw(data, token_id)

    def get_price(self, token_id: str, side: str) -> float | None:
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        data = self.http.get("/price", {"token_id": token_id, "side": side.capitalize()})
        if isinstance(data, dict) and "price" in data:
            try:
                return float(data["price"])
            except (TypeError, ValueError):
                return None
        return None

    def get_midpoint(self, token_id: str) -> float | None:
        data = self.http.get("/midpoint", {"token_id": token_id})
        if isinstance(data, dict) and "mid" in data:
            try:
                return float(data["mid"])
            except (TypeError, ValueError):
                return None
        return None

    def estimate_fill(self, token_id: str, side: str, size: float) -> FillEstimate:
        """Walk the live book to estimate the real cost of taking ``size``.

        ``side='buy'`` consumes asks; ``side='sell'`` consumes bids. This is the
        slippage model the paper broker will use in phase 2.
        """
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        if size <= 0:
            raise ValueError("size must be positive")
        return fill_from_book(self.get_book(token_id), side, size)


def fill_from_book(book: Book, side: str, size: float) -> FillEstimate:
    """Pure book-walking fill model (no network). Shared by the live
    estimator above and the paper broker."""
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if size <= 0:
        raise ValueError("size must be positive")
    levels = book.asks if side == "buy" else book.bids
    remaining = size
    cost = 0.0
    worst: float | None = None
    for level in levels:
        take = min(level.size, remaining)
        cost += take * level.price
        worst = level.price
        remaining -= take
        if remaining <= 1e-12:
            break
    filled = size - max(remaining, 0.0)
    avg = cost / filled if filled > 1e-12 else None
    return FillEstimate(
        side=side,
        target_size=size,
        filled_size=filled,
        average_price=avg,
        worst_price=worst,
    )
