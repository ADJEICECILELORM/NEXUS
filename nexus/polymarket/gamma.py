"""Gamma API client: market discovery and normalization.

Gamma's JSON fields (outcomes, outcomePrices, clobTokenIds, ...) are
double-encoded (JSON strings inside JSON) — `Market.from_raw` normalizes
them once so every consumer gets typed Python values.

Pagination (verified live, F0):
- `/markets`: offset pagination (offset capped ~2100 by the API)
- `/markets/keyset`: offset REJECTED (HTTP 422); the continuation param is
  `after_cursor` (docs-confirmed) — `cursor` is silently IGNORED (page 2
  == page 1) and `offset` returns 422.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from nexus.polymarket.http import (
    HttpJsonClient,
    PublicApiError,
    parse_float_list,
    parse_jsonish,
    parse_str_list,
)

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"


@dataclass
class Market:
    id: str
    question: str
    slug: str
    condition_id: str
    outcomes: list[str] = field(default_factory=list)
    outcome_prices: list[float | None] = field(default_factory=list)
    clob_token_ids: list[str] = field(default_factory=list)
    volume_num: float = 0.0
    liquidity_num: float = 0.0
    volume_24h: float = 0.0
    best_bid: float | None = None
    best_ask: float | None = None
    spread: float | None = None
    tick_size: float | None = None
    min_size: float | None = None
    active: bool = False
    closed: bool = False
    neg_risk: bool = False
    fees_enabled: bool = False
    end_date: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Market":
        if not isinstance(raw, dict):
            raise PublicApiError(f"Gamma market payload not a dict: {type(raw)!r}")
        return cls(
            id=str(raw.get("id", "")),
            question=str(raw.get("question", "")),
            slug=str(raw.get("slug", "")),
            condition_id=str(raw.get("conditionId", "")),
            outcomes=parse_str_list(raw.get("outcomes")),
            outcome_prices=parse_float_list(raw.get("outcomePrices")),
            clob_token_ids=[str(t) for t in parse_jsonish(raw.get("clobTokenIds")) or []
                            if isinstance(t, (str, int))],
            volume_num=_float_or(raw.get("volumeNum"), 0.0),
            liquidity_num=_float_or(raw.get("liquidityNum"), 0.0),
            volume_24h=_float_or(raw.get("volume24hr"), 0.0),
            best_bid=_float_or(raw.get("bestBid"), None),
            best_ask=_float_or(raw.get("bestAsk"), None),
            spread=_float_or(raw.get("spread"), None),
            tick_size=_float_or(raw.get("orderPriceMinTickSize"), None),
            min_size=_float_or(raw.get("orderMinSize"), None),
            active=bool(raw.get("active", False)),
            closed=bool(raw.get("closed", False)),
            neg_risk=bool(raw.get("negRisk", False)),
            fees_enabled=bool(raw.get("feesEnabled", False)),
            end_date=raw.get("endDate"),
            raw=raw,
        )

    @property
    def yes_token_id(self) -> str | None:
        if not self.clob_token_ids:
            return None
        idx = 0
        for i, name in enumerate(self.outcomes):
            if name.strip().lower() == "yes":
                idx = i
                break
        return self.clob_token_ids[idx] if idx < len(self.clob_token_ids) else None

    @property
    def is_binary(self) -> bool:
        return len(self.clob_token_ids) == 2

    @property
    def yes_price(self) -> float | None:
        if not self.outcome_prices:
            return None
        idx = 0
        for i, name in enumerate(self.outcomes):
            if name.strip().lower() == "yes":
                idx = i
                break
        return self.outcome_prices[idx] if idx < len(self.outcome_prices) else None


def _float_or(value: Any, default: float | None) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v


class GammaClient:
    def __init__(self, base_url: str = GAMMA_BASE_URL, **http_kwargs: object) -> None:
        self.http = HttpJsonClient(base_url, **http_kwargs)  # type: ignore[arg-type]

    def list_markets(self, limit: int = 100, offset: int = 0, **filters: object) -> list[dict]:
        params: dict[str, object] = {"limit": limit, "offset": offset, **filters}
        data = self.http.get("/markets", params)
        if not isinstance(data, list):
            raise PublicApiError(f"Gamma returned non-market payload: {type(data)!r}")
        return data

    def get_market(self, market_id: str) -> dict:
        data = self.http.get(f"/markets/{market_id}")
        if not isinstance(data, dict):
            raise PublicApiError(f"Gamma market {market_id} payload not a dict")
        return data

    def markets(
        self,
        max_markets: int = 500,
        page_size: int = 100,
        **filters: object,
    ) -> Iterator[Market]:
        """Iterate normalized markets with offset pagination until exhausted."""
        fetched = 0
        offset = 0
        while fetched < max_markets:
            page = self.list_markets(
                limit=min(page_size, max_markets - fetched), offset=offset, **filters
            )
            if not page:
                return
            for raw in page:
                yield Market.from_raw(raw)
            fetched += len(page)
            offset += len(page)
            if len(page) < page_size:
                return

    def markets_keyset(
        self,
        max_markets: int = 500,
        page_size: int = 100,
        **filters: object,
    ) -> Iterator[Market]:
        """Keyset pagination — required for deeper pages (offset is capped
        around 2100 by the API). Returns normalized markets."""
        fetched = 0
        cursor: str | None = None
        while fetched < max_markets:
            params = {
                "limit": min(page_size, max_markets - fetched),
                **filters,
            }
            if cursor:
                # Docs: "Use next_cursor from each response as after_cursor
                # in the next request." The `cursor` param name is ignored by
                # the API (page 2 == page 1) and `offset` is rejected (422).
                params["after_cursor"] = cursor
            payload = self.http.get("/markets/keyset", params)
            if not isinstance(payload, dict):
                raise PublicApiError(
                    f"keyset returned non-dict payload: {type(payload)!r}"
                )
            page = payload.get("markets", [])
            if not page:
                return
            for raw in page:
                yield Market.from_raw(raw)
            fetched += len(page)
            cursor = payload.get("next_cursor")
            if not cursor:
                return
