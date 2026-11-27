"""CLOB client tests: book normalization, fill walking, error paths."""
from __future__ import annotations

import json

import pytest

from nexus.polymarket.clob import (
    Book,
    BookLevel,
    ClobPublicClient,
    fill_from_book,
)
from nexus.polymarket.http import PublicApiError

from tests.conftest import fake_urlopen_factory

RAW_BOOK = {
    "bids": [{"price": "0.97", "size": "100"}, {"price": "0.99", "size": "50"}],
    "asks": [{"price": "0.98", "size": "80"}, {"price": "0.995", "size": "500"}],
}


def test_book_levels_sorted():
    book = Book.from_raw(RAW_BOOK, "tok1")
    assert [l.price for l in book.bids] == [0.99, 0.97]
    assert [l.price for l in book.asks] == [0.98, 0.995]
    assert book.mid == (0.99 + 0.98) / 2


def test_get_book_via_client(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", fake_urlopen_factory({"/book": RAW_BOOK})
    )
    clob = ClobPublicClient("https://base.example")
    book = clob.get_book("tok1")
    assert book.best_ask == 0.98
    assert book.bids[0].price == 0.99


def test_fill_from_book_buy_partial():
    book = Book.from_raw(RAW_BOOK, "tok1")
    # full book asks: 80 @ 0.98 + 500 @ 0.995 = 580 shares of depth
    est_full = fill_from_book(book, "buy", 100.0 / 0.98)
    assert est_full.fully_filled  # 102 shares fits
    assert 0.98 < est_full.average_price < 0.99  # touches second level
    # $1,000 -> ~1,020 shares > 580 depth -> partial
    est = fill_from_book(book, "buy", 1000.0 / 0.98)
    assert not est.fully_filled
    assert est.worst_price == 0.995
    assert 0.983 < est.average_price <= 0.995


def test_fill_from_book_sell():
    book = Book.from_raw(RAW_BOOK, "tok1")
    est = fill_from_book(book, "sell", 50.0)
    assert est.fully_filled
    assert est.average_price == 0.99


def test_estimate_fill_empty_book():
    book = Book(token_id="t", bids=[], asks=[])
    est = fill_from_book(book, "buy", 10.0)
    assert est.average_price is None
    assert not est.fully_filled


def test_get_price_side_capitalized(monkeypatch):
    seen = {}

    def fake(request, timeout=None):
        seen["url"] = request.full_url
        from tests.conftest import FakeResponse

        return FakeResponse(json.dumps({"price": "0.985"}).encode())

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    clob = ClobPublicClient("https://base.example")
    assert clob.get_price("tok1", "buy") == 0.985
    assert "side=Buy" in seen["url"]


def test_ok_endpoint(monkeypatch):
    class TextResponse:
        def __init__(self, body):
            self._b = body
            self.status = 200
            self.headers = {"Content-Encoding": ""}

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=None: TextResponse(b"OK")
    )
    clob = ClobPublicClient("https://base.example")
    assert clob.ok() is True

    def boom(req, timeout=None):
        raise PublicApiError("down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert clob.ok() is False


def test_client_rejects_bad_side():
    clob = ClobPublicClient("https://base.example")
    with pytest.raises(ValueError):
        clob.get_price("tok1", "sideways")
    with pytest.raises(ValueError):
        clob.estimate_fill("tok1", "sideways", 10.0)
