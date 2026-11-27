"""PaperEngine account persistence (JSON) for multi-day paper runs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from nexus.paper.engine import PaperAccount, PaperConfig, PaperPosition


def save_account(account: PaperAccount, path: str | os.PathLike) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cash": account.cash,
        "realized_pnl": account.realized_pnl,
        "fees_paid": account.fees_paid,
        "positions": [
            {
                "token_id": pos.token_id,
                "market_id": pos.market_id,
                "shares": pos.shares,
                "cost_basis": pos.cost_basis,
            }
            for pos in account.positions.values()
        ],
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1))
    tmp.replace(p)


def load_account(path: str | os.PathLike, cfg: PaperConfig | None = None) -> PaperAccount:
    """Load a saved account. Missing file -> fresh account per cfg."""
    cfg = cfg or PaperConfig()
    p = Path(path)
    if not p.exists():
        return PaperAccount(cash=cfg.starting_cash)
    payload = json.loads(p.read_text())
    account = PaperAccount(
        cash=float(payload["cash"]),
        realized_pnl=float(payload.get("realized_pnl", 0.0)),
        fees_paid=float(payload.get("fees_paid", 0.0)),
    )
    for raw in payload.get("positions", []):
        pos = PaperPosition(
            token_id=str(raw["token_id"]),
            market_id=str(raw["market_id"]),
            shares=float(raw["shares"]),
            cost_basis=float(raw["cost_basis"]),
        )
        account.positions[pos.token_id] = pos
    return account
