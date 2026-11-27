"""Data API v2 client — /v2/prices-history.

Verified live (F0): v1 endpoints RETIRED (HTTP 410); v2 params: token_id,
interval ("max" works; 12h buckets on old data), optional bucket_seconds,
start, end, limit, cursor. Response: {"data": [{timestamp, price,
resolution_seconds}]}. Error bodies are gzip-compressed (handled by http).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nexus.polymarket.http import HttpJsonClient

DATA_API_BASE_URL = "https://data-api.polymarket.com"


@dataclass
class PricePoint:
    ts: int
    price: float
    resolution_seconds: int | None = None


@dataclass
class PriceHistory:
    token_id: str
    points: list[PricePoint] = field(default_factory=list)


class DataApiClient:
    def __init__(self, base_url: str = DATA_API_BASE_URL, **http_kwargs: object) -> None:
        self.http = HttpJsonClient(base_url, **http_kwargs)  # type: ignore[arg-type]

    def prices_history(
        self,
        token_id: str,
        interval: str = "max",
        **extra: Any,
    ) -> PriceHistory:
        params: dict[str, Any] = {"token_id": token_id, "interval": interval, **extra}
        payload = self.http.get("/v2/prices-history", params)
        points: list[PricePoint] = []
        if isinstance(payload, dict):
            for row in payload.get("data", []) or []:
                if not isinstance(row, dict):
                    continue
                try:
                    points.append(PricePoint(
                        ts=int(row["timestamp"]),
                        price=float(row["price"]),
                        resolution_seconds=(
                            int(row["resolution_seconds"])
                            if row.get("resolution_seconds") is not None else None
                        ),
                    ))
                except (KeyError, TypeError, ValueError):
                    continue
        points.sort(key=lambda p: p.ts)
        return PriceHistory(token_id=token_id, points=points)
