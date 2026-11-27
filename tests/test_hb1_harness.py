"""Tests for the H-B1 paper harness (selection logic + paper entry)."""

from datetime import datetime, timedelta, timezone

import pytest

from nexus.journal import Journal
from nexus.polymarket.clob import Book, BookLevel
from nexus.polymarket.gamma import Market
from nexus.paper.engine import PaperConfig, PaperEngine
from nexus.paper.hb1_harness import (
    LAG_TOLERANCE_DAYS,
    PRIMARY_LAG_DAYS,
    ZONE_THRESHOLD,
    evaluate_market,
    run,
)

NOW = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)


def make_market(end_dt, tokens=("111", "222"), binary=True):
    return Market(
        id="m1", question="Will X happen?", slug="will-x",
        condition_id="0xcond", outcomes=["Yes", "No"],
        outcome_prices=[0.5, 0.5], clob_token_ids=list(tokens),
        volume_num=50_000.0, liquidity_num=1_000.0, volume_24h=1_000.0,
        best_bid=None, best_ask=None, spread=None, tick_size=0.01,
        min_size=5.0, active=True, closed=False, neg_risk=False,
        fees_enabled=True, end_date=end_dt.isoformat(), raw={},
    ) if binary else None


def make_book(token_id="111", mid=0.98, half_spread=0.01, depth_shares=2_000):
    """Synthetic book around `mid` with the given half-spread and ask depth."""
    bid = round(mid - half_spread, 4)
    ask = round(mid + half_spread, 4)
    return Book(
        token_id=token_id,
        bids=[BookLevel(price=bid, size=depth_shares)],
        asks=[BookLevel(price=ask, size=depth_shares)],
    )


class FakeGamma:
    def __init__(self, markets):
        self._markets = markets

    def markets_keyset(self, **kwargs):
        yield from self._markets


class FakeClob:
    def __init__(self, books):
        self._books = books

    def get_book(self, token_id):
        book = self._books.get(token_id)
        if book is None:
            raise RuntimeError("no orderbook")
        return book


def test_evaluate_market_selects_leading_token_in_window():
    end = NOW + timedelta(days=PRIMARY_LAG_DAYS)
    m = make_market(end)
    books = {"111": make_book("111", mid=0.98), "222": make_book("222", mid=0.02)}
    cand = evaluate_market(m, books, NOW)
    assert cand is not None
    assert cand.side == "YES"
    assert cand.token_id == "111"
    assert cand.price == pytest.approx(0.98)
    assert cand.half_spread == pytest.approx(0.01)


def test_evaluate_market_selects_no_token_when_no_qualifies():
    end = NOW + timedelta(days=PRIMARY_LAG_DAYS)
    m = make_market(end)
    books = {"111": make_book("111", mid=0.02), "222": make_book("222", mid=0.985)}
    cand = evaluate_market(m, books, NOW)
    assert cand is not None
    assert cand.side == "NO"
    assert cand.token_id == "222"


def test_evaluate_market_rejects_out_of_window():
    end = NOW + timedelta(days=PRIMARY_LAG_DAYS + LAG_TOLERANCE_DAYS + 0.5)
    m = make_market(end)
    books = {"111": make_book("111", mid=0.98), "222": make_book("222", mid=0.02)}
    assert evaluate_market(m, books, NOW) is None


def test_evaluate_market_rejects_below_zone():
    end = NOW + timedelta(days=PRIMARY_LAG_DAYS)
    m = make_market(end)
    books = {"111": make_book("111", mid=0.95), "222": make_book("222", mid=0.05)}
    assert evaluate_market(m, books, NOW) is None


def test_run_enters_candidate_through_paper_engine(tmp_path):
    end = NOW + timedelta(days=PRIMARY_LAG_DAYS)
    m = make_market(end)
    books = {"111": make_book("111", mid=0.98, depth_shares=5_000),
             "222": make_book("222", mid=0.02)}
    engine = PaperEngine(PaperConfig())
    journal = Journal(tmp_path / "hb1.jsonl")
    summary = run(
        FakeGamma([m]), FakeClob(books), engine, journal, now=NOW,
        max_markets=10, entry_usd=100.0,
    )
    assert summary["scanned"] == 1
    assert summary["in_window"] == 1
    assert summary["candidates"] == 1
    assert summary["entered"] == 1
    assert summary["rejected"] == 0
    assert engine.account.cash < engine.cfg.starting_cash
    assert "111" in engine.account.positions
    records = list(journal.read())
    assert records and records[0]["type"] == "hb1_decision"
    assert records[0]["outcome"] == "filled"
    assert summary["median_half_spread"] == pytest.approx(0.01)


def test_run_journals_rejection_when_book_too_thin(tmp_path):
    end = NOW + timedelta(days=PRIMARY_LAG_DAYS)
    m = make_market(end)
    # 100 USD at ~0.99 needs ~101 shares; depth 50 -> insufficient_book_depth
    books = {"111": make_book("111", mid=0.99, depth_shares=50),
             "222": make_book("222", mid=0.01)}
    engine = PaperEngine(PaperConfig())
    journal = Journal(tmp_path / "hb1.jsonl")
    summary = run(
        FakeGamma([m]), FakeClob(books), engine, journal, now=NOW,
        max_markets=10, entry_usd=100.0,
    )
    assert summary["candidates"] == 1
    assert summary["entered"] == 0
    assert summary["rejected"] == 1
    records = list(journal.read())
    assert records[0]["outcome"] == "rejected"


def test_run_handles_missing_books(tmp_path):
    end = NOW + timedelta(days=PRIMARY_LAG_DAYS)
    m = make_market(end)
    engine = PaperEngine(PaperConfig())
    journal = Journal(tmp_path / "hb1.jsonl")
    summary = run(
        FakeGamma([m]), FakeClob({}), engine, journal, now=NOW,
        max_markets=10,
    )
    assert summary["in_window"] == 1
    assert summary["candidates"] == 0
    assert engine.account.cash == engine.cfg.starting_cash
