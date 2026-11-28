"""Tests for the cost-report aggregator."""

import json

from nexus.paper.cost_report import aggregate, load_decisions, markdown_report


def write_journal(tmp_path, records):
    p = tmp_path / "j.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return p


def test_load_decisions_filters_and_tolerates_garbage(tmp_path):
    p = tmp_path / "j.jsonl"
    p.write_text(
        "\n".join([
            json.dumps({"type": "hb1_decision", "ts": "2026-11-27T13:00:00+00:00",
                        "half_spread": 0.02, "slippage_100": 0.021, "depth_usd": 1000.0,
                        "outcome": "filled"}),
            json.dumps({"type": "settle", "token_id": "t"}),          # other type
            "{not json at all",                                        # garbage line
            json.dumps({"type": "hb1_decision", "ts": "2026-11-28T13:00:00+00:00",
                        "half_spread": 0.03, "slippage_100": None, "depth_usd": 2000.0,
                        "outcome": "rejected"}),
        ]) + "\n"
    )
    recs = load_decisions(p)
    assert len(recs) == 2


def test_aggregate_overall_and_by_day(tmp_path):
    recs = [
        {"type": "hb1_decision", "ts": "2026-11-27T13:00:00+00:00",
         "half_spread": 0.020, "slippage_100": 0.021, "depth_usd": 5_000.0,
         "outcome": "filled"},
        {"type": "hb1_decision", "ts": "2026-11-27T14:00:00+00:00",
         "half_spread": 0.030, "slippage_100": 0.032, "depth_usd": 7_000.0,
         "outcome": "rejected"},
        {"type": "hb1_decision", "ts": "2026-11-28T13:00:00+00:00",
         "half_spread": 0.010, "slippage_100": None, "depth_usd": 500.0,
         "outcome": "filled"},
    ]
    agg = aggregate(recs)
    ov = agg["overall"]
    assert ov["decisions"] == 3
    assert ov["entered"] == 2
    assert ov["rejected"] == 1
    assert ov["half_spread"]["median"] == 0.02
    assert ov["half_spread"]["max"] == 0.03
    assert ov["slippage_100"]["n"] == 2          # None excluded
    assert set(agg["by_day"]) == {"2026-11-27", "2026-11-28"}
    assert agg["by_day"]["2026-11-27"]["decisions"] == 2


def test_markdown_report_flags_ratio_to_d2(tmp_path):
    recs = [
        {"type": "hb1_decision", "ts": "2026-11-27T13:00:00+00:00",
         "half_spread": 0.022, "slippage_100": 0.023, "depth_usd": 13_000.0,
         "outcome": "filled"},
    ]
    md = markdown_report(aggregate(recs))
    assert "11.0x" in md                         # 0.022 / 0.002
    assert "D2 assumed half-spread" in md
    assert "| 2026-11-27 | 1 |" in md


def test_empty_journal_produces_empty_report(tmp_path):
    agg = aggregate(load_decisions(tmp_path / "missing.jsonl"))
    assert agg["overall"]["decisions"] == 0
    md = markdown_report(agg)
    assert "Decisions: 0" in md
