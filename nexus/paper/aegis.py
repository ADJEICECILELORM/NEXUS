"""
AEGIS — event-driven risk layer for the paper engine.

Rebuild of v2's risk agent, fixing every defect recorded in
docs/V2_AUDIT.md:

- checks are attached to REAL ledger events (paper account + journal),
  not to submission intents;
- the caller passes the market question, so correlated-theme exposure
  actually buckets (v2 lumped everything into "other");
- the daily boundary is UTC, not server-local;
- the kill switch and daily-loss state PERSIST to disk across runs;
- nothing here approves anything by itself: `pre_trade_check` is consulted
  by the harness before `PaperEngine.buy`, and `apply_fill` /
  `apply_settlement` ingest the engine's outcomes.

Paper only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Correlated-theme buckets: substring keys on the lowercased question.
THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "bitcoin": ("bitcoin", "btc"),
    "ethereum": ("ethereum", "eth"),
    "solana": ("solana", "sol"),
    "crypto": ("crypto", "price of", "token"),
    "us-politics": ("trump", "biden", "congress", "senate", "president",
                    "election", "governor"),
    "fed-rates": ("fed", "rate", "fomc", "inflation", "cpi"),
    "geopolitics": ("russia", "ukraine", "iran", "china", "israel", "war",
                    "ceasefire"),
    "sports": ("nba", "nfl", "mlb", "nhl", "premier league", "ufc",
               "championship", "match", "game", " vs ", "vs."),
}


def theme_of(question: str) -> str:
    """Bucket a market question into a correlated-exposure theme.

    Keyword matches are anchored at a word boundary - plain substring
    matching misfires ("something" contains "eth").
    """
    q = (question or "").lower()
    for theme, keys in THEME_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(k)}", q) for k in keys):
            return theme
    return "other"


@dataclass
class AegisConfig:
    max_position_usd: float = 200.0        # F4: viability is size-limited
    max_theme_pct: float = 0.15            # 15% of bankroll per theme
    daily_loss_limit_pct: float = 0.10     # -10% bankroll -> kill for the day
    max_open_positions: int = 20


@dataclass
class AegisState:
    killed: bool = False
    kill_reason: str = ""
    day: str = ""                          # UTC date of the daily-loss bucket
    day_realized_pnl: float = 0.0
    # theme -> {token_id: committed notional} for exact release on settlement
    theme_exposure: dict[str, dict[str, float]] = field(default_factory=dict)


class Aegis:
    """Risk governor over a paper account. Consulted pre-trade; fed outcomes."""

    def __init__(
        self,
        account,
        cfg: AegisConfig | None = None,
        state: AegisState | None = None,
        journal=None,
    ) -> None:
        self.account = account
        self.cfg = cfg or AegisConfig()
        self.state = state or AegisState()
        self.journal = journal

    # -------------------------------------------------------------- helpers
    def _today(self, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        return now.astimezone(timezone.utc).strftime("%Y-%m-%d")

    def _reset_if_new_day(self, now: datetime | None = None) -> None:
        today = self._today(now)
        if self.state.day != today:
            self.state.day = today
            self.state.day_realized_pnl = 0.0

    @property
    def bankroll(self) -> float:
        """Bankroll = cash + committed open-position cost basis."""
        return self.account.cash + sum(
            p.cost_basis for p in self.account.positions.values()
        )

    # ---------------------------------------------------------- persistence
    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "killed": self.state.killed,
            "kill_reason": self.state.kill_reason,
            "day": self.state.day,
            "day_realized_pnl": self.state.day_realized_pnl,
            "theme_exposure": self.state.theme_exposure,
        }
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=1))
        tmp.replace(p)

    @classmethod
    def load(cls, account, path: str | Path, cfg: AegisConfig | None = None,
             journal=None) -> "Aegis":
        p = Path(path)
        state = AegisState()
        if p.exists():
            raw = json.loads(p.read_text())
            state = AegisState(
                killed=bool(raw.get("killed", False)),
                kill_reason=str(raw.get("kill_reason", "")),
                day=str(raw.get("day", "")),
                day_realized_pnl=float(raw.get("day_realized_pnl", 0.0)),
                theme_exposure={
                    theme: {t: float(n) for t, n in tokens.items()}
                    for theme, tokens in raw.get("theme_exposure", {}).items()
                },
            )
        return cls(account, cfg, state, journal)

    # ----------------------------------------------------------- pre-trade
    def pre_trade_check(
        self,
        token_id: str,
        notional_usd: float,
        question: str = "",
        open_book_depth_usd: float | None = None,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """Approve or reject a prospective buy. Mechanical, in order."""
        self._reset_if_new_day(now)

        if self.state.killed:
            return False, "kill_switch_active"
        if notional_usd <= 0:
            return False, "non_positive_size"
        if notional_usd > self.cfg.max_position_usd + 1e-9:
            return False, "position_cap"
        if len(self.account.positions) >= self.cfg.max_open_positions \
                and token_id not in self.account.positions:
            return False, "max_open_positions"

        # Theme exposure: committed cost basis per theme, caller supplies the
        # question (v2's bug: the question never reached the risk layer).
        bankroll = self.bankroll
        theme = theme_of(question)
        committed = sum(self.state.theme_exposure.get(theme, {}).values())
        if committed + notional_usd > bankroll * self.cfg.max_theme_pct + 1e-9:
            return False, f"theme_overexposure:{theme}"

        # Daily loss kill: checked on realized pnl bucketed to the UTC day.
        limit = self.bankroll * self.cfg.daily_loss_limit_pct
        if self.state.day_realized_pnl <= -limit:
            self._kill(f"daily_loss_limit ({self.state.day_realized_pnl:.2f})")
            return False, "kill_switch_active"

        return True, "approved"

    # ------------------------------------------------------- event ingestion
    def apply_fill(self, token_id: str, notional_usd: float,
                   question: str = "") -> None:
        """Record committed exposure after an actual fill (not a submission)."""
        theme = theme_of(question)
        bucket = self.state.theme_exposure.setdefault(theme, {})
        bucket[token_id] = bucket.get(token_id, 0.0) + notional_usd
        if self.journal is not None:
            self.journal.append({
                "type": "aegis_fill", "token_id": token_id,
                "notional_usd": notional_usd, "theme": theme,
            })

    def apply_settlement(self, token_id: str, question: str,
                         pnl: float, now: datetime | None = None) -> None:
        """Release this token's committed exposure and bucket realized pnl
        to the UTC day."""
        self._reset_if_new_day(now)
        theme = theme_of(question)
        bucket = self.state.theme_exposure.setdefault(theme, {})
        bucket.pop(token_id, None)
        if not bucket:
            self.state.theme_exposure.pop(theme, None)
        self.state.day_realized_pnl += pnl
        if self.journal is not None:
            self.journal.append({
                "type": "aegis_settlement", "token_id": token_id,
                "theme": theme, "pnl": round(pnl, 6),
                "day_realized_pnl": round(self.state.day_realized_pnl, 6),
            })
        limit = self.bankroll * self.cfg.daily_loss_limit_pct
        if self.state.day_realized_pnl <= -limit:
            self._kill(f"daily_loss_limit ({self.state.day_realized_pnl:.2f})")

    # -------------------------------------------------------------- kill
    def _kill(self, reason: str) -> None:
        self.state.killed = True
        self.state.kill_reason = reason
        if self.journal is not None:
            self.journal.append({
                "type": "aegis_kill", "reason": reason,
                "ts_bucket": self.state.day,
            })

    def revive(self) -> None:
        """Manual revive clears the kill switch and the daily bucket."""
        self.state.killed = False
        self.state.kill_reason = ""
        self.state.day_realized_pnl = 0.0
