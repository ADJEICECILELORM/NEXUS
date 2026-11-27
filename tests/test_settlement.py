"""Tests for account persistence and the settlement watcher."""

from nexus.journal import Journal
from nexus.polymarket.gamma import Market
from nexus.paper.engine import PaperConfig, PaperEngine, PaperPosition
from nexus.paper.state import load_account, save_account
from nexus.paper.settlement import resolution_from_market, settle_pending

CFG = PaperConfig()


def make_account():
    from nexus.paper.engine import PaperAccount
    acct = PaperAccount(cash=9_800.0, realized_pnl=-12.5, fees_paid=0.6)
    acct.positions["tokWIN"] = PaperPosition(
        token_id="tokWIN", market_id="0xaaa", shares=100.0, cost_basis=98.0,
    )
    acct.positions["tokLOSE"] = PaperPosition(
        token_id="tokLOSE", market_id="0xbbb", shares=50.0, cost_basis=49.5,
    )
    return acct


def test_save_and_load_account_roundtrip(tmp_path):
    path = tmp_path / "acct.json"
    acct = make_account()
    save_account(acct, path)
    loaded = load_account(path, CFG)
    assert loaded.cash == 9_800.0
    assert loaded.realized_pnl == -12.5
    assert loaded.fees_paid == 0.6
    assert set(loaded.positions) == {"tokWIN", "tokLOSE"}
    assert loaded.positions["tokWIN"].shares == 100.0
    assert loaded.positions["tokLOSE"].cost_basis == 49.5


def test_load_account_missing_file_gives_fresh(tmp_path):
    loaded = load_account(tmp_path / "nope.json", CFG)
    assert loaded.cash == CFG.starting_cash
    assert loaded.positions == {}


def test_resolution_from_market_variants():
    base = {
        "id": "m1", "question": "q", "slug": "s", "conditionId": "0xc",
        "outcomes": "[\"Yes\", \"No\"]", "clobTokenIds": "[\"t1\", \"t2\"]",
        "volumeNum": 1.0, "liquidityNum": 1.0, "volume24hr": 1.0,
        "orderPriceMinTickSize": 0.01, "orderMinSize": 5.0,
        "active": True, "closed": True, "negRisk": False,
        "feesEnabled": False, "endDate": "2026-12-01T00:00:00Z",
    }
    resolved = Market.from_raw({**base, "outcomePrices": [1, 0]})
    assert resolution_from_market(resolved) == {"t1": 1.0, "t2": 0.0}
    flipped = Market.from_raw({**base, "outcomePrices": [0, 1]})
    assert resolution_from_market(flipped) == {"t1": 0.0, "t2": 1.0}
    open_m = Market.from_raw({**base, "closed": False, "outcomePrices": [1, 0]})
    assert resolution_from_market(open_m) is None
    ambiguous = Market.from_raw({**base, "outcomePrices": [0.6, 0.4]})
    assert resolution_from_market(ambiguous) is None
    missing = Market.from_raw({**base, "outcomePrices": [None, 0]})
    assert resolution_from_market(missing) is None


class FakeGamma:
    def __init__(self, by_condition):
        self.by_condition = by_condition

    def list_markets(self, **kwargs):
        cond = kwargs.get("condition_ids")
        return list(self.by_condition.get(cond, []))


RAW_CLOSED_WIN = {
    "conditionId": "0xaaa", "question": "q?", "closed": True,
    "clobTokenIds": '["tokWIN","tokNo"]', "outcomePrices": "[1, 0]",
}
RAW_OPEN = {
    "conditionId": "0xbbb", "question": "q2?", "closed": False,
    "clobTokenIds": '["tokLOSE","tokYes"]', "outcomePrices": "[0.5, 0.5]",
}


def test_settle_pending_settles_resolved_and_keeps_open(tmp_path):
    engine = PaperEngine(CFG, Journal(tmp_path / "j.jsonl"))
    engine.account = make_account()
    gamma = FakeGamma({"0xaaa": [RAW_CLOSED_WIN], "0xbbb": [RAW_OPEN]})
    summary = settle_pending(gamma, engine, Journal(tmp_path / "j.jsonl"))

    assert summary["settled"] == 1
    assert summary["still_open"] == 1
    # tokWIN: 100 shares at 1.0 -> proceeds 100.0, cost 98.0 -> +2.0
    assert summary["pnl"] == 2.0
    assert engine.account.realized_pnl == -12.5 + 2.0
    assert engine.account.cash == 9_800.0 + 100.0
    assert "tokWIN" not in engine.account.positions
    assert "tokLOSE" in engine.account.positions


def test_settle_pending_lookup_failure_keeps_position(tmp_path):
    engine = PaperEngine(CFG, Journal(tmp_path / "j.jsonl"))
    engine.account = make_account()
    gamma = FakeGamma({})  # nothing resolvable
    summary = settle_pending(gamma, engine, Journal(tmp_path / "j.jsonl"))
    assert summary["lookup_failed"] == 2
    assert summary["settled"] == 0
    assert set(engine.account.positions) == {"tokWIN", "tokLOSE"}


def test_settle_pending_broken_gamma_is_safe(tmp_path):
    class Broken:
        def list_markets(self, **kwargs):
            raise RuntimeError("network down")

    engine = PaperEngine(CFG, Journal(tmp_path / "j.jsonl"))
    engine.account = make_account()
    summary = settle_pending(Broken(), engine, Journal(tmp_path / "j.jsonl"))
    assert summary["lookup_failed"] == 2
    assert summary["settled"] == 0
