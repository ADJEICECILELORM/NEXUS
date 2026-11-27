"""Paper engine tests — offline, deterministic book snapshots."""
from __future__ import annotations

from nexus.journal import Journal
from nexus.polymarket.clob import Book, BookLevel
from nexus.paper.engine import PaperConfig, PaperEngine


def make_book(bid=0.97, bid_sz=1000.0, ask=0.98, ask_sz=1000.0, token="tok1"):
    return Book(
        token_id=token,
        bids=[BookLevel(bid, bid_sz)],
        asks=[BookLevel(ask, ask_sz)],
    )


def test_buy_walks_book_and_charges_fee():
    eng = PaperEngine(PaperConfig(starting_cash=1000.0))
    book = make_book(ask=0.98, ask_sz=40.0)  # $100 order needs 102 shares > depth 40
    fill = eng.buy("tok1", "m1", 100.0, book)
    assert fill is None  # book too thin for $100
    book2 = make_book(ask=0.98, ask_sz=200.0)
    fill2 = eng.buy("tok1", "m1", 100.0, book2)
    assert fill2 is not None and fill2.fully_filled
    assert fill2.avg_price == 0.98
    shares = 100.0 / 0.98
    fee = 0.05 * min(0.98, 0.02) * shares
    assert abs(fill2.fee - fee) < 1e-9
    assert abs(eng.account.cash - (1000.0 - 100.0 - fee)) < 1e-6
    assert eng.account.positions["tok1"].shares == shares


def test_rejections_below_min_and_cap():
    eng = PaperEngine(PaperConfig(starting_cash=1000.0, max_position_usd=50.0))
    book = make_book()
    assert eng.buy("tok1", "m1", 4.0, book) is None       # below min $5
    assert eng.buy("tok1", "m1", 60.0, book) is None      # above position cap
    assert eng.buy("tok1", "m1", 50.0, book) is not None
    assert eng.buy("tok1", "m1", 10.0, book) is None      # would exceed cap


def test_sell_realizes_pnl_and_updates_position():
    eng = PaperEngine(PaperConfig(starting_cash=1000.0, max_position_usd=200.0))
    fill = eng.buy("tok1", "m1", 100.0, make_book(ask=0.98))
    assert fill is not None
    proceeds_fill = eng.sell("tok1", fill.shares, make_book(bid=0.99))
    assert proceeds_fill is not None
    assert eng.account.realized_pnl > 0
    assert "tok1" not in eng.account.positions
    assert eng.account.cash == eng.cfg.starting_cash + eng.account.realized_pnl


def test_settle_resolution_win_and_lose():
    eng = PaperEngine(PaperConfig(starting_cash=100.0))
    fill = eng.buy("tok1", "m1", 50.0, make_book(ask=0.98, ask_sz=100.0))
    eng.settle("tok1", 1.0)  # WIN
    pnl_win = eng.account.realized_pnl
    assert pnl_win > 0
    assert eng.account.cash == 100.0 + pnl_win

    eng2 = PaperEngine(PaperConfig(starting_cash=100.0))
    fill2 = eng2.buy("tok2", "m2", 50.0, make_book(ask=0.98, ask_sz=100.0, token="tok2"))
    eng2.settle("tok2", 0.0)  # LOSE
    assert eng2.account.realized_pnl < 0
    assert abs(eng2.account.cash - (100.0 - 50.0 - fill2.fee)) < 1e-6


def test_equity_and_unrealized_marks():
    eng = PaperEngine(PaperConfig(starting_cash=1000.0))
    eng.buy("tok1", "m1", 100.0, make_book(ask=0.98))
    # mid of 0.97/0.98 = 0.975 -> small unrealized loss vs 0.98 cost
    u = eng.unrealized({"tok1": make_book()})
    assert u < 0
    eq = eng.equity({"tok1": make_book()})
    assert 0 < eq < 1000.0
    assert eng.equity({}) == eng.account.cash


def test_journal_records_fills_and_rejects(tmp_path):
    j = Journal(str(tmp_path / "paper.jsonl"))
    eng = PaperEngine(PaperConfig(starting_cash=1000.0), journal=j)
    eng.buy("tok1", "m1", 2.0, make_book())  # rejected (below min)
    eng.buy("tok1", "m1", 100.0, make_book())  # filled
    recs = list(j.read())
    assert [r["type"] for r in recs] == ["reject", "buy"]
    assert recs[1]["shares"] > 0
