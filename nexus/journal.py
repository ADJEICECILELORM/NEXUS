"""Append-only JSONL journal — the primitive every phase writes to.

One record per line, timestamps added automatically. Reads are a generator
so a journal of any size streams lazily.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Journal:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any], *, timestamp: bool = True) -> dict[str, Any]:
        entry = dict(record)
        if timestamp and "ts" not in entry:
            entry["ts"] = utc_now_iso()
        line = json.dumps(entry, sort_keys=True, default=str)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return entry

    def read(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)
