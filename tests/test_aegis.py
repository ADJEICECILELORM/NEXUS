"""Tests for the AEGIS risk layer (rebuild per V2_AUDIT defect list)."""

from datetime import datetime, timezone

from nexus.journal import Journal
from nexus.paper.aegis import Aegis, AegisConfig, AegisState, theme_of
from nexus.paper.engine import PaperAccount, PaperPosition

NOW = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
TOMORROW = datetime(2026, 11, 28, 12, 0, 0, tzinfo=timezone.utc)


def make_aegis(cfg: AegisConfig | None = None, cash: float = 10_000.0):
    acct = PaperAccount(cash=cash)
    return Aegis(acct, cfg or AegisConfig())


def test_theme_of_buckets_questions():
    assert theme_of("Will the price of Bitcoin be above $50k?") == "bitcoin"
    assert theme_of("Will ETH hit $2000?") == "ethereum"
    assert theme_of("Will Trump win the election?") == "us-politics"
    assert theme_of("Will the Fed cut rates in March?") == "fed-rates"
    assert theme_of("Lakers vs Celtics?") == "sports"
    assert theme_of("Something entirely else") == "other"


def test_approve_normal_trade():
    a = make_aegis()
    ok, why = a.pre_trade_check("t1", 200.0, "Bitcoin question?", now=NOW)
    assert ok and why == "approved"


def test_reject_position_cap():
    a = make_aegis(AegisConfig(max_position_usd=200.0))
    ok, why = a.pre_trade_check("t1", 250.0, "q?", now=NOW)
    assert not ok and why == "position_cap"


def test_reject_when_killed_persists():
    a = make_aegis()
    a._kill("test")
    ok, why = a.pre_trade_check("t1", 100.0, "q?", now=NOW)
    assert not ok and why == "kill_switch_active"


def test_theme_exposure_blocks_overweight_theme():
    cfg = AegisConfig(max_position_usd=200.0, max_theme_pct=0.15)
    a = make_aegis(cfg, cash=10_000.0)
    # bankroll 10,000 -> theme cap 1,500 -> 7 entries of 200 fit, 8th blocked
    for i in range(7):
        ok, _ = a.pre_trade_check(f"t{i}", 200.0, "Bitcoin price?", now=NOW)
        assert ok
        a.apply_fill(f"t{i}", 200.0, question="Bitcoin price?")
    ok, why = a.pre_trade_check("t7", 200.0, "Bitcoin price?", now=NOW)
    assert not ok and why == "theme_overexposure:bitcoin"
    # unrelated theme unaffected
    ok, why = a.pre_trade_check("x1", 200.0, "Will it snow in Oslo?", now=NOW)
    assert ok


def test_settlement_releases_only_that_token():
    cfg = AegisConfig(max_theme_pct=0.15)
    a = make_aegis(cfg, cash=10_000.0)
    a.apply_fill("t1", 200.0, question="Bitcoin price?")
    a.apply_fill("t2", 200.0, question="Bitcoin price?")
    committed = sum(a.state.theme_exposure["bitcoin"].values())
    assert committed == 400.0
    a.apply_settlement("t1", "Bitcoin price?", pnl=-200.0, now=NOW)
    remaining = sum(a.state.theme_exposure["bitcoin"].values())
    assert remaining == 200.0                   # only t2 committed
    assert a.state.day_realized_pnl == -200.0   # bucketed to the day


def test_daily_loss_limit_kills():
    a = make_aegis(AegisConfig(daily_loss_limit_pct=0.10), cash=10_000.0)
    # bankroll 10,000 -> limit -1,000
    a.apply_settlement("t1", "q?", pnl=-600.0, now=NOW)
    ok, why = a.pre_trade_check("t2", 100.0, "q?", now=NOW)
    assert ok                                    # -600 not yet -1,000
    a.apply_settlement("t3", "q?", pnl=-450.0, now=NOW)
    ok, why = a.pre_trade_check("t4", 100.0, "q?", now=NOW)
    assert not ok and why == "kill_switch_active"
    assert a.state.killed and "daily_loss_limit" in a.state.kill_reason


def test_daily_bucket_resets_next_utc_day():
    a = make_aegis(AegisConfig(daily_loss_limit_pct=0.10), cash=10_000.0)
    a.apply_settlement("t1", "q?", pnl=-950.0, now=NOW)
    ok, _ = a.pre_trade_check("t2", 100.0, "q?", now=TOMORROW)  # next UTC day
    assert ok
    assert a.state.day == "2026-11-28"
    assert a.state.day_realized_pnl == 0.0


def test_revive_clears_kill_and_bucket():
    a = make_aegis()
    a.apply_settlement("t1", "q?", pnl=-9_999.0, now=NOW)
    assert a.state.killed
    a.revive()
    assert not a.state.killed
    assert a.state.day_realized_pnl == 0.0
    ok, why = a.pre_trade_check("t2", 100.0, "q?", now=NOW)
    assert ok


def test_save_load_roundtrip(tmp_path):
    a = make_aegis()
    a.apply_fill("t1", 200.0, question="Bitcoin price?")
    a.apply_settlement("t1", "Bitcoin price?", pnl=-50.0, now=NOW)
    path = tmp_path / "aegis.json"
    a.save(path)
    acct = a.account
    b = Aegis.load(acct, path)
    assert b.state.killed == a.state.killed
    assert b.state.day == "2026-11-27"
    assert b.state.day_realized_pnl == -50.0
    assert b.state.theme_exposure == a.state.theme_exposure
