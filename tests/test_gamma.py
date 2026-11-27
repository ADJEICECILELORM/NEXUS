"""Gamma client tests: normalization, pagination, retry semantics."""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from nexus.journal import Journal
from nexus.polymarket.gamma import GammaClient, Market
from nexus.polymarket.http import HttpJsonClient, PublicApiError, parse_jsonish

from tests.conftest import FakeResponse, fake_urlopen_factory


RAW_MARKET = {
    "id": "665374",
    "question": "Will X happen before 2027?",
    "slug": "will-x-happen-before-2027",
    "conditionId": "0xabc123",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.055", "0.945"]',
    "clobTokenIds": '["5511", "1910"]',
    "volumeNum": 81731541.71,
    "liquidityNum": 245290.11,
    "volume24hr": 91480.45,
    "bestBid": 0.05,
    "bestAsk": 0.06,
    "spread": 0.01,
    "orderPriceMinTickSize": 0.01,
    "orderMinSize": 5,
    "active": True,
    "closed": False,
    "negRisk": False,
    "feesEnabled": False,
    "endDate": "2027-01-01T04:59:00Z",
}


def test_parse_jsonish_handles_string_and_list():
    assert parse_jsonish('["Yes","No"]') == ["Yes", "No"]
    assert parse_jsonish(["Yes", "No"]) == ["Yes", "No"]
    assert parse_jsonish("plain") == "plain"
    assert parse_jsonish("[broken") == "[broken"


def test_market_from_raw_normalizes_double_encoded():
    m = Market.from_raw(RAW_MARKET)
    assert m.id == "665374"
    assert m.outcomes == ["Yes", "No"]
    assert m.outcome_prices == [0.055, 0.945]
    assert m.clob_token_ids == ["5511", "1910"]
    assert m.yes_token_id == "5511"
    assert m.is_binary
    assert m.yes_price == 0.055
    assert m.fees_enabled is False


def test_market_from_raw_tolerant_on_missing_fields():
    m = Market.from_raw({"id": "1"})
    assert m.id == "1"
    assert m.outcomes == []
    assert m.yes_token_id is None
    assert not m.is_binary


def test_list_markets_returns_raw_dicts(monkeypatch):
    fake = fake_urlopen_factory({"/markets": [RAW_MARKET]})
    monkeypatch.setattr("urllib.request.urlopen", fake)
    client = GammaClient("https://base.example")
    page = client.list_markets(limit=1, offset=0)
    assert page[0]["conditionId"] == "0xabc123"


def test_markets_offset_pagination(monkeypatch):
    page_1 = [RAW_MARKET]
    second = dict(RAW_MARKET, id="665375")
    routes = {
        "/markets": lambda url: [second] if "offset=1" in url else page_1,
    }
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen_factory(routes))
    client = GammaClient("https://base.example")
    ids = [m.id for m in client.markets(max_markets=2, page_size=1)]
    assert ids == ["665374", "665375"]


def test_http_error_wrapped(monkeypatch):
    def deny(request, timeout=None):
        raise urllib.error.HTTPError("https://x", 403, "no", {}, io.BytesIO(b"no"))

    monkeypatch.setattr("urllib.request.urlopen", deny)
    client = GammaClient("https://x")
    with pytest.raises(PublicApiError):
        client.list_markets(limit=1)


def test_http_client_retries_on_500(monkeypatch):
    attempts = {"n": 0}

    def flaky(request, timeout=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise urllib.error.HTTPError(
                "https://x", 500, "boom", {}, io.BytesIO(b"err")
            )
        return FakeResponse(json.dumps({"ok": True}).encode())

    monkeypatch.setattr("urllib.request.urlopen", flaky)
    client = HttpJsonClient("https://x", backoff_base=0.0)
    assert client.get("/ok") == {"ok": True}
    assert attempts["n"] == 2


def test_http_client_no_retry_on_403(monkeypatch):
    attempts = {"n": 0}

    def denied(request, timeout=None):
        attempts["n"] += 1
        raise urllib.error.HTTPError(
            "https://x", 403, "forbidden", {}, io.BytesIO(b"no")
        )

    monkeypatch.setattr("urllib.request.urlopen", denied)
    client = HttpJsonClient("https://x", max_retries=2, backoff_base=0.0)
    with pytest.raises(PublicApiError):
        client.get("/denied")
    assert attempts["n"] == 1


def _market_raw(mid: str) -> dict:
    raw = dict(RAW_MARKET)
    raw["id"] = mid
    raw["conditionId"] = f"0x{mid}"
    return raw


def test_markets_keyset_uses_after_cursor(monkeypatch):
    """Regression: keyset continuation param must be `after_cursor`.

    The API silently ignores `cursor` (page 2 == page 1) and rejects
    `offset` with 422. Docs: use next_cursor as after_cursor.
    """
    seen_urls: list[str] = []

    def fake_urlopen(request, timeout=None):
        seen_urls.append(request.full_url)
        if "after_cursor=c1" in request.full_url:
            payload = {"markets": [_market_raw("m2")], "next_cursor": "c2"}
        else:
            payload = {"markets": [_market_raw("m1")], "next_cursor": "c1"}
        return FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = GammaClient("https://base.example")
    ids = [m.id for m in client.markets_keyset(max_markets=2, page_size=1, closed="true")]
    assert ids == ["m1", "m2"]
    assert any("after_cursor=c1" in u for u in seen_urls)
    assert not any("offset=" in u for u in seen_urls)
