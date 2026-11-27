"""Journal primitive tests."""
from __future__ import annotations

from nexus.journal import Journal, utc_now_iso


def test_append_adds_timestamp(tmp_path):
    j = Journal(str(tmp_path / "j.jsonl"))
    entry = j.append({"type": "open", "symbol": "X"})
    assert "ts" in entry
    assert entry["ts"].startswith("20")


def test_read_roundtrip_excluding_ts(tmp_path):
    j = Journal(str(tmp_path / "j.jsonl"))
    j.append({"type": "open", "symbol": "X", "qty": 1})
    j.append({"type": "close", "symbol": "X", "qty": 1})
    recs = list(j.read())
    assert len(recs) == 2
    a = {k: v for k, v in recs[0].items() if k != "ts"}
    assert a == {"type": "open", "symbol": "X", "qty": 1}


def test_read_missing_file(tmp_path):
    j = Journal(str(tmp_path / "nope.jsonl"))
    assert list(j.read()) == []
