"""Calibration analysis (F2): how well do market prices predict outcomes?

Observations: for every resolved market and every lag L in LAGS_DAYS, take
each outcome token's last price print at or before (end_date - L) within the
staleness window, and record whether that token resolved to 1 or 0. Both
winner and loser tokens are observed (bias-free — see F2/DECISIONS).

Metrics:
- Brier score across all (price, outcome) pairs per lag
- per lag x price-band: mean price vs realized rate, Wilson 95% CI on the
  rate — a well-calibrated market has the rate INSIDE the CI
- tail risk: flips among p >= 0.97 (decided-market failures, feeds H-B1)
"""
from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from typing import Any

LAGS_DAYS = (30, 14, 7, 3, 1)
BANDS = (
    (0.00, 0.10),
    (0.10, 0.30),
    (0.30, 0.70),
    (0.70, 0.90),
    (0.90, 0.97),
    (0.97, 1.01),
)
STALE_MAX_DAYS = 3.0
TAIL_THRESHOLD = 0.97
DAY = 86400


def wilson(rate: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% CI for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    denom = 1 + z * z / n
    centre = (rate + z * z / (2 * n)) / denom
    half = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _end_ts(end_date: str | None) -> int | None:
    if not end_date:
        return None
    try:
        dt = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _last_point_at_or_before(
    series: list[tuple[int, float]], ts: int
) -> tuple[int, float] | None:
    """Last (ts, price) with ts <= cutoff; series sorted ascending."""
    lo, hi = 0, len(series) - 1
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= ts:
            best = series[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def collect_observations(store: Any) -> list[dict]:
    """(market, lag, token, price, resolved) observations, both token sides."""
    histories: dict[str, list[tuple[int, float]]] = {}
    current_token, series = None, []
    for token_id, ts, price in store.conn.execute(
        "SELECT token_id, ts, price FROM price_points ORDER BY token_id, ts"
    ).fetchall():
        if token_id != current_token:
            if current_token is not None and series:
                histories[current_token] = series
            current_token, series = token_id, []
        series.append((int(ts), float(price)))
    if current_token is not None and series:
        histories[current_token] = series

    observations: list[dict] = []
    for row in store.iter_resolved():
        import json as _json

        try:
            tokens = _json.loads(row["clob_token_ids"]) if row["clob_token_ids"] else []
        except (ValueError, TypeError):
            tokens = []
        winner = row["winner_outcome_index"]
        end = _end_ts(row["end_date"])
        if winner is None or not tokens or end is None:
            continue
        for lag in LAGS_DAYS:
            cutoff = end - lag * DAY
            for i, token in enumerate(tokens):
                found = _last_point_at_or_before(histories.get(str(token), []), cutoff)
                if found is None:
                    continue
                point_ts, price = found
                stale_days = (cutoff - point_ts) / DAY
                if stale_days > STALE_MAX_DAYS:
                    continue
                observations.append({
                    "market_id": row["market_id"],
                    "lag": lag,
                    "token_index": i,
                    "is_winner": 1 if i == winner else 0,
                    "price": price,
                    "stale_days": round(stale_days, 3),
                })
    return observations


def summarize(observations: list[dict]) -> list[dict]:
    """Per lag x band: n, mean price, realized rate, Wilson CI, in/out flag."""
    rows: list[dict] = []
    for lag in LAGS_DAYS:
        for lo, hi in BANDS:
            cell = [
                o for o in observations
                if o["lag"] == lag and lo <= o["price"] < hi
            ]
            n = len(cell)
            if n == 0:
                rows.append({"lag": lag, "band": (lo, hi), "n": 0})
                continue
            mean_p = sum(o["price"] for o in cell) / n
            rate = sum(o["is_winner"] for o in cell) / n
            ci_lo, ci_hi = wilson(rate, n)
            rows.append({
                "lag": lag,
                "band": (lo, hi),
                "n": n,
                "mean_p": mean_p,
                "rate": rate,
                "ci": (ci_lo, ci_hi),
                "calibrated": ci_lo <= mean_p <= ci_hi,
            })
    return rows


def tail_risk(observations: list[dict]) -> list[dict]:
    """Flips among p >= 0.97 by lag (decided-market failure mode)."""
    rows: list[dict] = []
    for lag in LAGS_DAYS:
        cell = [o for o in observations
                if o["lag"] == lag and o["price"] >= TAIL_THRESHOLD]
        n = len(cell)
        flips = sum(1 for o in cell if o["is_winner"] == 0)
        rows.append({
            "lag": lag,
            "n": n,
            "flips": flips,
            "flip_rate": flips / n if n else None,
        })
    return rows


def brier(observations: list[dict]) -> dict[int, float]:
    out: dict[int, float] = {}
    for lag in LAGS_DAYS:
        cell = [o for o in observations if o["lag"] == lag]
        if cell:
            out[lag] = sum(
                (o["price"] - o["is_winner"]) ** 2 for o in cell
            ) / len(cell)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="calibration analysis")
    parser.add_argument("--db", default="data/nexus_research.db")
    args = parser.parse_args(argv)
    from nexus.research.store import Store

    store = Store(args.db)
    try:
        observations = collect_observations(store)
    finally:
        store.close()
    print(f"observations: {len(observations)}")
    print(f"Brier by lag: "
          + ", ".join(f"{lag}d={score:.4f}" for lag, score in brier(observations).items()))
    print()
    print("| lag | band | n | mean p | rate | calibrated |")
    print("|---|---|---|---|---|---|")
    for row in summarize(observations):
        if row["n"] == 0:
            continue
        lo, hi = row["ci"]
        print(f"| {row['lag']}d | {row['band'][0]:.2f}-{row['band'][1]:.2f} "
              f"| {row['n']} | {row['mean_p']:.3f} | {row['rate']:.3f} "
              f"[{lo:.3f},{hi:.3f}] | {'yes' if row['calibrated'] else 'FLAG'} |")
    print()
    print("| lag | decided | flips | flip rate |")
    print("|---|---|---|---|")
    for row in tail_risk(observations):
        rate = f"{row['flip_rate']:.3f}" if row["flip_rate"] is not None else "—"
        print(f"| {row['lag']}d | {row['n']} | {row['flips']} | {rate} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
