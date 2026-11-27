"""Paper trading engine (phase 2 scaffold) — deterministic, book-walking,
fee-aware, stdlib-only. Paper ONLY: no order submission, no credentials,
no network. Fills come from book snapshots passed in by the caller (live
books now, recorded books later), so the same engine backtests and paper-trades.

Cost model matches the pre-registered D2/D4 model: taker fee =
fee_rate x min(price, 1-price) per share, plus real book-walking slippage
via ``fill_from_book``. This is the engine H-B1's paper gate will run on.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from nexus.journal import Journal
from nexus.polymarket.clob import Book, fill_from_book


@dataclass
class PaperConfig:
    starting_cash: float = 10_000.0
    taker_fee_rate: float = 0.05  # live feeSchedule: rate 0.05, takerOnly
    min_order_usd: float = 5.0    # Polymarket orderMinSize (USD)
    max_position_usd: float = 200.0  # F4: viability is size-limited; hard cap


@dataclass
class PaperPosition:
    token_id: str
    market_id: str
    shares: float = 0.0
    cost_basis: float = 0.0  # total USD paid incl. fees

    @property
    def avg_cost_per_share(self) -> float | None:
        return self.cost_basis / self.shares if self.shares > 1e-12 else None


@dataclass
class PaperFill:
    token_id: str
    action: str  # "buy" | "sell"
    shares: float
    avg_price: float
    fee: float
    cash_delta: float  # signed USD impact on cash (negative for buys)
    fully_filled: bool
    reason: str = ""


@dataclass
class PaperAccount:
    cash: float
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    positions: dict[str, PaperPosition] = field(default_factory=dict)


def _taker_fee(price: float, rate: float) -> float:
    return rate * min(price, 1.0 - price)


class PaperEngine:
    """Simulated execution against book snapshots. Emits journal records."""

    def __init__(self, cfg: PaperConfig | None = None, journal: Journal | None = None) -> None:
        self.cfg = cfg or PaperConfig()
        self.account = PaperAccount(cash=self.cfg.starting_cash)
        self.journal = journal

    # ------------------------------------------------------------------ buy
    def buy(self, token_id: str, market_id: str, notional_usd: float,
            book: Book, reason: str = "") -> PaperFill | None:
        """Take ``notional_usd`` of ``token_id`` from the ask side of ``book``.

        Returns None (and journals the rejection) when the order is below
        min size, exceeds the position cap, the book cannot fill it, or
        cash is insufficient.
        """
        if notional_usd < self.cfg.min_order_usd:
            return self._reject(token_id, "below_min_order", notional_usd)
        current_usd = self._position_usd(token_id, book)
        if current_usd + notional_usd > self.cfg.max_position_usd + 1e-9:
            return self._reject(token_id, "position_cap", notional_usd)
        px = book.asks[0].price if book.asks else None
        if px is None or px <= 0:
            return self._reject(token_id, "no_liquidity", notional_usd)
        target_shares = notional_usd / px
        est = fill_from_book(book, "buy", target_shares)
        if not est.fully_filled or est.average_price is None:
            return self._reject(token_id, "insufficient_book_depth", notional_usd)
        shares = est.filled_size
        fee = _taker_fee(est.average_price, self.cfg.taker_fee_rate) * shares
        cost = shares * est.average_price + fee
        if cost > self.account.cash:
            return self._reject(token_id, "insufficient_cash", notional_usd)
        self.account.cash -= cost
        self.account.fees_paid += fee
        pos = self.account.positions.setdefault(
            token_id, PaperPosition(token_id=token_id, market_id=market_id)
        )
        pos.shares += shares
        pos.cost_basis += cost
        fill = PaperFill(
            token_id=token_id, action="buy", shares=shares,
            avg_price=est.average_price, fee=fee, cash_delta=-cost,
            fully_filled=True, reason=reason,
        )
        self._journal(fill, market_id)
        return fill

    # ----------------------------------------------------------------- sell
    def sell(self, token_id: str, shares: float, book: Book,
             reason: str = "") -> PaperFill | None:
        """Sell ``shares`` of ``token_id`` into the bid side of ``book``.

        Settlement semantics: a held share sells at the bid; resolution
        settlement (win -> 1.0, lose -> 0.0) is ``settle()``.
        """
        pos = self.account.positions.get(token_id)
        if pos is None or pos.shares < shares - 1e-12:
            return self._reject(token_id, "no_position", shares)
        if not book.bids:
            return self._reject(token_id, "no_liquidity", shares)
        est = fill_from_book(book, "sell", shares)
        if not est.fully_filled or est.average_price is None:
            return self._reject(token_id, "insufficient_book_depth", shares)
        fee = _taker_fee(est.average_price, self.cfg.taker_fee_rate) * shares
        proceeds = shares * est.average_price - fee
        # realized pnl: proceeds minus the cost basis PROPORTION sold
        avg_cost = pos.cost_basis / pos.shares
        self.account.cash += proceeds
        self.account.realized_pnl += proceeds - avg_cost * shares
        self.account.fees_paid += fee
        pos.shares -= shares
        pos.cost_basis -= avg_cost * shares
        market_id = pos.market_id
        if pos.shares <= 1e-12:
            del self.account.positions[token_id]
        fill = PaperFill(
            token_id=token_id, action="sell", shares=shares,
            avg_price=est.average_price, fee=fee, cash_delta=proceeds,
            fully_filled=True, reason=reason,
        )
        self._journal(fill, market_id)
        return fill

    # ------------------------------------------------------------- settle
    def settle(self, token_id: str, outcome_price: float) -> float:
        """Resolve a held position at its final outcome price (1.0 win,
        0.0 lose). Returns realized pnl delta. No fee at settlement."""
        pos = self.account.positions.get(token_id)
        if pos is None or pos.shares <= 0:
            return 0.0
        proceeds = pos.shares * outcome_price
        self.account.cash += proceeds
        delta = proceeds - pos.cost_basis
        self.account.realized_pnl += delta
        del self.account.positions[token_id]
        if self.journal is not None:
            self.journal.append({
                "type": "settle", "token_id": token_id,
                "shares": pos.shares, "outcome_price": outcome_price,
                "pnl": round(delta, 6),
            })
        return delta

    # ---------------------------------------------------------------- marks
    def unrealized(self, books: dict[str, Book]) -> float:
        total = 0.0
        for token_id, pos in self.account.positions.items():
            book = books.get(token_id)
            if book is None or book.mid is None:
                continue  # unmarkable: contributes 0 until a book exists
            total += pos.shares * book.mid - pos.cost_basis
        return total

    def equity(self, books: dict[str, Book]) -> float:
        cash_value = self.account.cash
        for token_id, pos in self.account.positions.items():
            book = books.get(token_id)
            mid = book.mid if book is not None else None
            cash_value += pos.shares * (mid if mid is not None else 0.0)
        return cash_value

    # -------------------------------------------------------------- private
    def _position_usd(self, token_id: str, book: Book) -> float:
        pos = self.account.positions.get(token_id)
        if pos is None or pos.shares <= 0:
            return 0.0
        mid = book.mid if book.mid is not None else (
            pos.avg_cost_per_share or 0.0
        )
        return pos.shares * mid

    def _reject(self, token_id: str, why: str, size: float) -> None:
        if self.journal is not None:
            self.journal.append({
                "type": "reject", "token_id": token_id, "why": why, "size": size,
            })

    def _journal(self, fill: PaperFill, market_id: str) -> None:
        if self.journal is not None:
            self.journal.append({
                "type": fill.action, "token_id": fill.token_id,
                "market_id": market_id, "shares": round(fill.shares, 6),
                "avg_price": round(fill.avg_price, 6), "fee": round(fill.fee, 6),
                "cash_delta": round(fill.cash_delta, 6), "reason": fill.reason,
            })
