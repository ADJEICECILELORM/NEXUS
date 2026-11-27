"""SQLite research store: resolved markets + price points.

Schema contract (v1):
- resolved_markets(market_id PK, question, slug, condition_id, event_slug,
  outcomes, winner_outcome_index, clob_token_ids, volume_num, liquidity_num,
  volume_24h, neg_risk, fees_enabled, end_date, collected_at)
- price_points(token_id, ts, price, PK(token_id, ts))

Winner labels: `winner_outcome_index` is the outcome index whose final price
is exactly 1 (>= 0.999 tolerance) or NULL when unresolved.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterator

DEFAULT_DB = "data/nexus_research.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resolved_markets (
    market_id TEXT PRIMARY KEY,
    question TEXT,
    slug TEXT,
    condition_id TEXT,
    event_slug TEXT,
    outcomes TEXT,
    winner_outcome_index INTEGER,
    clob_token_ids TEXT,
    volume_num REAL,
    liquidity_num REAL,
    volume_24h REAL,
    neg_risk INTEGER,
    fees_enabled INTEGER,
    end_date TEXT,
    collected_at TEXT
);
CREATE TABLE IF NOT EXISTS price_points (
    token_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    price REAL NOT NULL,
    PRIMARY KEY (token_id, ts)
);
"""


class Store:
    def __init__(self, path: str = DEFAULT_DB) -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------ markets
    def upsert_resolved_market(self, row: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO resolved_markets (
                market_id, question, slug, condition_id, event_slug, outcomes,
                winner_outcome_index, clob_token_ids, volume_num, liquidity_num,
                volume_24h, neg_risk, fees_enabled, end_date, collected_at
            ) VALUES (
                :market_id, :question, :slug, :condition_id, :event_slug, :outcomes,
                :winner_outcome_index, :clob_token_ids, :volume_num, :liquidity_num,
                :volume_24h, :neg_risk, :fees_enabled, :end_date, :collected_at
            )
            ON CONFLICT(market_id) DO UPDATE SET
                question=excluded.question,
                winner_outcome_index=excluded.winner_outcome_index,
                clob_token_ids=excluded.clob_token_ids,
                volume_num=excluded.volume_num,
                liquidity_num=excluded.liquidity_num,
                volume_24h=excluded.volume_24h,
                neg_risk=excluded.neg_risk,
                fees_enabled=excluded.fees_enabled,
                end_date=excluded.end_date,
                collected_at=excluded.collected_at
            """,
            row,
        )

    def iter_resolved(self) -> Iterator[sqlite3.Row]:
        cursor = self.conn.execute(
            "SELECT * FROM resolved_markets ORDER BY end_date, market_id"
        )
        for row in cursor:
            yield row

    # ------------------------------------------------------------ prices
    def record_price_point(self, token_id: str, ts: int, price: float) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO price_points (token_id, ts, price) VALUES (?, ?, ?)",
            (token_id, ts, price),
        )

    def n_points(self, token_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM price_points WHERE token_id = ?", (token_id,)
        ).fetchone()
        return int(row["n"]) if row else 0

    def token_ids(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT token_id FROM price_points ORDER BY token_id"
        ).fetchall()
        return [r["token_id"] for r in rows]

    def all_token_ids(self) -> list[tuple[str, int]]:
        """Both outcome tokens of every resolved market + is_winner flag.

        Loser tokens matter: calibration must see both sides or it is
        selection-biased (F2).
        """
        out: list[tuple[str, int]] = []
        for row in self.conn.execute(
            "SELECT market_id, clob_token_ids, winner_outcome_index FROM resolved_markets"
        ):
            try:
                tokens = json.loads(row["clob_token_ids"]) if row["clob_token_ids"] else []
            except (ValueError, TypeError):
                tokens = []
            winner = row["winner_outcome_index"]
            for i, token in enumerate(tokens):
                if isinstance(token, str) and token:
                    out.append((token, 1 if (winner is not None and i == winner) else 0))
        return out

    def counts(self) -> dict[str, int]:
        return {
            "resolved_markets": self.conn.execute(
                "SELECT COUNT(*) FROM resolved_markets"
            ).fetchone()[0],
            "labeled_markets": self.conn.execute(
                "SELECT COUNT(*) FROM resolved_markets WHERE winner_outcome_index IS NOT NULL"
            ).fetchone()[0],
            "price_points": self.conn.execute(
                "SELECT COUNT(*) FROM price_points"
            ).fetchone()[0],
            "tokens_with_history": self.conn.execute(
                "SELECT COUNT(DISTINCT token_id) FROM price_points"
            ).fetchone()[0],
        }

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
