"""Research-layer tests: winner labels, store, negRisk scan, calibration."""
from __future__ import annotations

import json

import pytest

from nexus.research.calibration import (
    _end_ts,
    _last_point_at_or_before,
    wilson,
)
from nexus.research.collect import winner_index
from nexus.research.negrisk import EventEdge, scan_event
from nexus.research.store import Store


# ---------------------------------------------------------------- winner
def test_winner_index_exact_one():
    assert winner_index([0.9999, 1.0, 0.0]) == 1
    assert winner_index([1.0, 0.0]) == 0


def test_winner_index_unresolved_returns_none():
    assert winner_index([0.5, 0.5]) is None
    assert winner_index([0.97, 0.03]) is None  # closed but not resolved
    assert winner_index([]) is None


# ----------------------------------------------------------------- store
def _market_row(mid="m1", tokens=('"t1"', '"t2"'), winner=0):
    return {
        "market_id": mid,
        "question": "q",
        "slug": "s",
        "condition_id": "0x" + mid,
        "event_slug": None,
        "outcomes": '["Yes","No"]',
        "winner_outcome_index": winner,
        "clob_token_ids": "[" + ",".join(tokens) + "]",
        "volume_num": 1000.0,
        "liquidity_num": 100.0,
        "volume_24h": 10.0,
        "neg_risk": 0,
        "fees_enabled": 0,
        "end_date": "2026-01-01T00:00:00Z",
        "collected_at": "2026-01-01T00:00:00+00:00",
    }


def test_store_roundtrip_and_idempotent(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.upsert_resolved_market(_market_row())
    store.upsert_resolved_market(_market_row())  # idempotent upsert
    store.commit()
    rows = list(store.iter_resolved())
    assert len(rows) == 1
    assert rows[0]["winner_outcome_index"] == 0
    assert json.loads(rows[0]["clob_token_ids"]) == ["t1", "t2"]
    store.record_price_point("t1", 100, 0.5)
    store.record_price_point("t1", 100, 0.5)  # PK prevents dupes
    store.commit()
    assert store.n_points("t1") == 1
    assert store.n_points("tX") == 0
    assert store.token_ids() == ["t1"]
    assert store.all_token_ids() == [("t1", 1), ("t2", 0)]
    store.close()


def test_counts(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.upsert_resolved_market(_market_row("m1"))
    store.upsert_resolved_market(_market_row("m2", winner=None))
    store.record_price_point("t1", 1, 0.5)
    store.commit()
    c = store.counts()
    assert c["resolved_markets"] == 2
    assert c["labeled_markets"] == 1
    assert c["price_points"] == 1
    assert c["tokens_with_history"] == 1
    store.close()


# --------------------------------------------------------------- negRisk
class FakeBook:
    def __init__(self, bid, ask):
        from nexus.polymarket.clob import BookLevel

        self.bids = [BookLevel(bid, 100.0)] if bid is not None else []
        self.asks = [BookLevel(ask, 100.0)] if ask is not None else []
        self.best_bid = bid
        self.best_ask = ask


class FakeClob:
    def __init__(self, books):
        self.books = books

    def get_book(self, token_id):
        return self.books[token_id]


class BrokenClob:
    def get_book(self, token_id):
        from nexus.polymarket.http import PublicApiError

        raise PublicApiError("GET failed: HTTP 404")


def _event(tokens=("a", "b", "c")):
    return {
        "slug": "ev",
        "title": "Event",
        "volume": "50000",
        "negRisk": True,
        "markets": [{"clobTokenIds": json.dumps([t, t + "_no"])} for t in tokens],
    }


def test_scan_event_liquid_books():
    clob = FakeClob({
        "a": FakeBook(0.10, 0.12),
        "b": FakeBook(0.30, 0.32),
        "c": FakeBook(0.55, 0.57),
    })
    edge = scan_event(_event(), clob)
    assert edge is not None
    assert edge.complete
    assert edge.long_gap == pytest.approx(1.0 - (0.12 + 0.32 + 0.57))
    assert edge.short_gap == pytest.approx((0.10 + 0.30 + 0.55) - 1.0)


def test_scan_event_partial_is_not_complete():
    # 4 outcomes, one dead -> 3 priced (>= MIN_PRICED_OUTCOMES) but not complete
    event = _event(("a", "b", "c", "d"))
    clob = FakeClob({
        "a": FakeBook(0.10, 0.12),
        "b": FakeBook(0.30, 0.32),
        "c": FakeBook(0.55, 0.57),
        "d": FakeBook(None, None),  # dead outcome
    })
    edge = scan_event(event, clob)
    assert edge is not None
    assert not edge.complete
    assert edge.sum_best_ask is None
    assert edge.n_priced == 3


def test_scan_event_broken_clob_yields_none():
    assert scan_event(_event(), BrokenClob()) is None


def test_event_edge_properties():
    e = EventEdge("s", "t", 100.0, 3, 3, 1.02, 0.98, True)
    assert e.long_gap == pytest.approx(-0.02)
    assert e.short_gap == pytest.approx(-0.02)
    assert e.complete


# ----------------------------------------------------------- calibration
def test_wilson():
    lo, hi = wilson(0.5, 100)
    assert 0.39 < lo < 0.41
    assert 0.59 < hi < 0.61
    assert wilson(0.0, 0) == (0.0, 1.0)


def test_last_point_at_or_before():
    series = [(10, 0.5), (20, 0.6), (30, 0.7)]
    assert _last_point_at_or_before(series, 25) == (20, 0.6)
    assert _last_point_at_or_before(series, 10) == (10, 0.5)
    assert _last_point_at_or_before(series, 5) is None


def test_end_ts_parses_iso_and_z():
    assert _end_ts("2026-01-01T00:00:00Z") is not None
    assert _end_ts(None) is None
    assert _end_ts("garbage") is None
