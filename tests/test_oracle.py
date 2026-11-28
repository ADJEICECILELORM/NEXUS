"""Tests for the ORACLE scorecard (D6)."""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from nexus.journal import Journal
from nexus.oracle import scorecard
from nexus.oracle.llm import (
    AnthropicClient,
    FakeLlm,
    LlmError,
    build_prompt,
    parse_probability,
)
from nexus.polymarket.gamma import Market

NOW = datetime(2026, 11, 28, 12, 0, 0, tzinfo=timezone.utc)


def _market(mid: str, question: str, days_out: float = 7.0, **kw) -> Market:
    return Market(
        id=mid,
        question=question,
        slug=mid,
        condition_id=f"cond-{mid}",
        outcomes=["Yes", "No"],
        clob_token_ids=[f"{mid}-yes", f"{mid}-no"],
        end_date=(NOW + timedelta(days=days_out)).isoformat(),
        **kw,
    )


class FakeGamma:
    def __init__(self, markets):
        self.markets = markets
        self.resolved_by_condition: dict[str, list[dict]] = {}

    def markets_keyset(self, **kw):
        yield from self.markets

    def list_markets(self, condition_ids=None, limit=5, **kw):
        return self.resolved_by_condition.get(condition_ids, [])


class FakeClob:
    def __init__(self, mids: dict[str, float]):
        self.mids = mids

    def get_midpoint(self, token_id):
        if token_id in self.mids:
            return self.mids[token_id]
        raise RuntimeError("no book")


# ---------------------------------------------------------------- parse/prompt


def test_parse_probability_valid_forms():
    assert parse_probability("blah PROBABILITY: 0.42") == pytest.approx(0.42)
    assert parse_probability("PROBABILITY = 0.7") == pytest.approx(0.7)
    assert parse_probability("PROBABILITY: 85%") == pytest.approx(0.85)  # percent form
    assert parse_probability("PROBABILITY: 1.0") == 1.0


def test_parse_probability_invalid():
    with pytest.raises(LlmError):
        parse_probability("I think yes")
    with pytest.raises(LlmError):
        parse_probability("PROBABILITY: 1.5")


def test_build_prompt_contains_question_and_format():
    prompt = build_prompt("Will X happen?", "Resolves YES if X.")
    assert "Will X happen?" in prompt
    assert "Resolves YES if X." in prompt
    assert "PROBABILITY:" in prompt
    # empty rules fall back to the natural-reading instruction
    assert "most natural reading" in build_prompt("Q?", "")


def test_fake_llm_matching_and_callable():
    fake = FakeLlm({"bitcoin": 0.9})
    assert fake.forecast("Will Bitcoin be above X?", "") == 0.9
    with pytest.raises(LlmError):
        fake.forecast("Unrelated question?", "")
    assert FakeLlm(lambda q: 0.5).forecast("anything", "") == 0.5


def test_anthropic_client_request_and_parse(monkeypatch):
    captured = {}

    def urlopen(request, timeout=None):
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode())
        body = json.dumps(
            {"content": [{"type": "text", "text": "Reasoning...\nPROBABILITY: 0.33"}]}
        ).encode()
        return type("R", (), {
            "read": lambda self: body, "context": None,
            "__enter__": lambda self: self, "__exit__": lambda self, *a: False,
        })()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = AnthropicClient(api_key="sk-test", model="claude-test")
    prob = client.forecast("Will X?", "Rules here.")
    assert prob == pytest.approx(0.33)
    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers["x-api-key"] == "sk-test"
    assert captured["body"]["model"] == "claude-test"
    assert "Will X?" in captured["body"]["messages"][0]["content"]


def test_anthropic_client_no_retry_on_400(monkeypatch):
    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            "url", 400, "bad", {}, None
        ) if False else urllib.error.HTTPError("url", 400, "bad", {}, None)

    calls = {"n": 0}

    def urlopen2(request, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError("url", 400, "bad request", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", urlopen2)
    client = AnthropicClient(api_key="k")
    with pytest.raises(LlmError):
        client.forecast("Q", "")
    assert calls["n"] == 1  # 400 is not retried


def test_anthropic_client_retries_on_500(monkeypatch):
    calls = {"n": 0}

    def urlopen(request, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError("url", 500, "boom", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = AnthropicClient(api_key="k", max_retries=3)
    with pytest.raises(LlmError):
        client.forecast("Q", "")
    assert calls["n"] == 3


# ---------------------------------------------------------------- forecast pass


def test_run_forecasts_journals_and_dedupes(tmp_path):
    journal = Journal(tmp_path / "oracle.jsonl")
    gamma = FakeGamma([
        _market("m1", "Will Bitcoin be above $100k on Dec 5?"),
        _market("m2", "Will the Chiefs win their NFL game on Dec 5?"),
        _market("m3", "Too far out", days_out=30.0),   # outside window
    ])
    clob = FakeClob({"m1-yes": 0.9, "m2-yes": 0.95})
    llm = FakeLlm(0.8)

    summary = scorecard.run_forecasts(gamma, clob, llm, journal, now=NOW)
    assert summary["forecast"] == 2
    recs = list(journal.read())
    forecasts = [r for r in recs if r["type"] == "oracle_forecast"]
    assert len(forecasts) == 2
    by_id = {r["market_id"]: r for r in forecasts}
    assert by_id["m1"]["llm_prob"] == 0.8
    assert by_id["m1"]["market_prob"] == 0.9
    assert by_id["m1"]["theme_class"] == "non-sports"
    assert by_id["m2"]["theme_class"] == "sports"

    # same-day dedupe
    summary2 = scorecard.run_forecasts(gamma, clob, llm, journal, now=NOW)
    assert summary2["forecast"] == 0
    assert summary2["skipped_today"] == 2


def test_run_forecasts_no_book_and_llm_error(tmp_path):
    journal = Journal(tmp_path / "oracle.jsonl")
    gamma = FakeGamma([
        _market("m1", "Question one?"),
        _market("m2", "Question two?"),
    ])
    clob = FakeClob({})  # no books at all
    summary = scorecard.run_forecasts(gamma, clob, FakeLlm(0.5), journal, now=NOW)
    assert summary["no_book"] == 2 and summary["forecast"] == 0

    clob2 = FakeClob({"m1-yes": 0.9, "m2-yes": 0.9})
    class Boom(FakeLlm):
        def forecast(self, question, rules):
            raise LlmError("api down")
    summary2 = scorecard.run_forecasts(gamma, clob2, Boom(0.5), journal, now=NOW)
    assert summary2["llm_error"] == 2
    errors = [r for r in journal.read() if r["type"] == "oracle_forecast_error"]
    assert len(errors) == 2


def test_run_forecasts_max_cap(tmp_path):
    journal = Journal(tmp_path / "oracle.jsonl")
    gamma = FakeGamma([_market(f"m{i}", f"Question {i}?") for i in range(10)])
    clob = FakeClob({f"m{i}-yes": 0.9 for i in range(10)})
    summary = scorecard.run_forecasts(
        gamma, clob, FakeLlm(0.5), journal, now=NOW, max_forecasts=3
    )
    assert summary["forecast"] == 3


# ---------------------------------------------------------------- scoring


def _resolved_raw(cond_id: str, yes_wins: bool) -> dict:
    return {
        "conditionId": cond_id,
        "closed": True,
        "outcomePrices": ["1", "0"] if yes_wins else ["0", "1"],
        "clobTokenIds": ["yes-token", "no-token"],
        "outcomes": ["Yes", "No"],
    }


def test_score_settled_scores_and_marks(tmp_path):
    journal = Journal(tmp_path / "oracle.jsonl")
    ts = NOW.isoformat()
    journal.append({"type": "oracle_forecast", "day": "2026-11-28",
                    "market_id": "m1", "condition_id": "cond-m1",
                    "question": "Q1?", "yes_token_id": "m1-yes",
                    "llm_prob": 0.8, "market_prob": 0.9,
                    "theme_class": "non-sports", "model": "test",
                    "ts": ts}, timestamp=False)
    gamma = FakeGamma([])
    gamma.resolved_by_condition["cond-m1"] = [_resolved_raw("cond-m1", yes_wins=True)]
    # token ids differ between forecast and resolution fixture -> unresolved
    summary = scorecard.score_settled(gamma, journal)
    assert summary["unresolved"] == 1

    # matching token ids -> settles
    journal2 = Journal(tmp_path / "oracle2.jsonl")
    journal2.append({"type": "oracle_forecast", "day": "2026-11-28",
                     "market_id": "m1", "condition_id": "cond-m1",
                     "question": "Q1?", "yes_token_id": "yes-token",
                     "llm_prob": 0.8, "market_prob": 0.9,
                     "theme_class": "non-sports", "model": "test",
                     "ts": ts}, timestamp=False)
    summary2 = scorecard.score_settled(gamma, journal2)
    assert summary2["scored"] == 1
    settlements = [r for r in journal2.read() if r["type"] == "oracle_settlement"]
    assert len(settlements) == 1
    s = settlements[0]
    assert s["outcome"] == 1.0
    assert s["brier_llm"] == pytest.approx(0.2 ** 2, abs=1e-6)
    assert s["brier_market"] == pytest.approx(0.1 ** 2, abs=1e-6)

    # idempotent
    summary3 = scorecard.score_settled(gamma, journal2)
    assert summary3["already_scored"] == 1 and summary3["scored"] == 0


def test_last_forecast_per_market_wins(tmp_path):
    journal = Journal(tmp_path / "oracle.jsonl")
    journal.append({"type": "oracle_forecast", "market_id": "m1",
                    "condition_id": "c", "question": "Q", "yes_token_id": "t",
                    "llm_prob": 0.5, "market_prob": 0.5, "theme_class": "x",
                    "model": "m", "ts": "2026-11-27T00:00:00+00:00"},
                   timestamp=False)
    journal.append({"type": "oracle_forecast", "market_id": "m1",
                    "condition_id": "c", "question": "Q", "yes_token_id": "t",
                    "llm_prob": 0.9, "market_prob": 0.5, "theme_class": "x",
                    "model": "m", "ts": "2026-11-28T00:00:00+00:00"},
                   timestamp=False)
    latest = scorecard._last_forecast_per_market(journal.read())
    assert len(latest) == 1
    assert latest["m1"]["llm_prob"] == 0.9


# ---------------------------------------------------------------- verdict


def _pair(llm_b, mkt_b, theme="non-sports"):
    # outcome 1.0: brier = (1 - p)^2 -> invert for probs
    return scorecard.SettledPair(
        market_id="m", question="Q", llm_prob=1 - llm_b ** 0.5,
        market_prob=1 - mkt_b ** 0.5, outcome=1.0,
        brier_llm=llm_b, brier_market=mkt_b, theme_class=theme, model="t",
    )


def test_verdict_pass():
    # LLM consistently sharper: mean diff negative, CI well below 0
    pairs = [_pair(0.05, 0.20) for _ in range(120)]
    res = scorecard.verdict_for(pairs)
    assert res["verdict"] == "PASS"
    assert res["mean_diff"] < 0 and res["ci95_hi"] < 0


def test_verdict_no_edge_when_market_better():
    pairs = [_pair(0.20, 0.05) for _ in range(120)]
    res = scorecard.verdict_for(pairs)
    assert res["verdict"] == "NO EDGE"


def test_verdict_insufficient():
    res = scorecard.verdict_for([_pair(0.05, 0.20)] * 10)
    assert res["verdict"] == "INSUFFICIENT"


def test_full_verdict_split_and_report():
    pairs = [_pair(0.05, 0.20) for _ in range(120)]
    pairs += [_pair(0.20, 0.05, theme="sports") for _ in range(30)]
    v = scorecard.full_verdict(pairs)
    assert v["overall"]["verdict"] == "PASS"
    assert v["non_sports"]["verdict"] == "PASS"
    assert v["sports_n"] == 30
    report = scorecard.markdown_report(pairs)
    assert "ORACLE scorecard (D6)" in report
    assert "PASS" in report
