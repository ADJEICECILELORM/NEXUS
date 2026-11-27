"""H-B1 decided-market carry — pre-registered, cost-aware backtest.

Strategy under test (D2, docs/DECISIONS.md): buy ANY token priced >= 0.97
exactly LAG days before the market's end_date. Winner tokens resolve to 1,
loser tokens to 0 — the honest version: flip risk is fully priced in.

Cost model (pre-registered D2; re-graded with measured costs per D4):
fill = price + HALF_SPREAD, taker fee = FEE_RATE x min(fill, 1 - fill).

Grading (D2, mechanical, in order):
- INSUFFICIENT if OOS n < 30
- KILL if OOS mean net return per trade <= 0
- KILL if OOS flip rate >= 2%
- PASS if OOS mean > 0 AND Wilson-style 95% lower bound of the mean > 0

Split: markets sorted by end_date, first 70% IS, last 30% OOS. Only OOS is
graded. Config edits after seeing results are forbidden (D2).
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone

HALF_SPREAD = 0.002  # pre-registered fill cost (docs/DECISIONS.md D2)
FEE_RATE = 0.05  # live feeSchedule: rate 0.05, takerOnly
LAGS = (30, 14, 7, 3)  # 7 = primary; others secondary
THRESHOLDS = (0.97,)  # primary threshold
IS_FRACTION = 0.70
OOS_MIN_TRADES = 30
FLIP_KILL_RATE = 0.02
STALE_MAX_DAYS = 3.0
DAY = 86400


def set_costs(half_spread: float, fee_rate: float = FEE_RATE) -> None:
    """Override cost globals (used by the D4 re-grade with measured spread)."""
    global HALF_SPREAD, FEE_RATE
    HALF_SPREAD = half_spread
    FEE_RATE = fee_rate


@dataclass
class Trade:
    market_id: str
    lag_days: int
    threshold: float
    p: float
    fill: float
    fee: float
    label: int  # 1 = token resolved to 1 (win), 0 = flip (lose)
    end_ts: int

    @property
    def pnl_per_share(self) -> float:
        if self.label == 1:
            return 1.0 - self.fill - self.fee
        return 0.0 - self.fill - self.fee


def _fee(fill: float) -> float:
    return FEE_RATE * min(fill, 1.0 - fill)


def json_loads_safe(value: object) -> list:
    try:
        parsed = json.loads(value) if value else []
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


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


@dataclass
class SegmentStats:
    n: int = 0
    mean_pnl: float = 0.0
    total_pnl: float = 0.0
    flips: int = 0
    flip_rate: float = 0.0
    win_rate: float = 0.0
    wilson_lo: float = 0.0
    verdict: str = ""


def _stdev(pnls: list[float]) -> float:
    n = len(pnls)
    if n < 2:
        return 0.0
    mean = sum(pnls) / n
    return math.sqrt(sum((x - mean) ** 2 for x in pnls) / (n - 1))


def _segment(trades: list[Trade]) -> SegmentStats:
    stats = SegmentStats()
    stats.n = len(trades)
    if not trades:
        stats.verdict = "INSUFFICIENT"
        return stats
    pnls = [t.pnl_per_share for t in trades]
    stats.total_pnl = sum(pnls)
    stats.mean_pnl = stats.total_pnl / len(trades)
    stats.flips = sum(1 for t in trades if t.label == 0)
    stats.flip_rate = stats.flips / stats.n
    stats.win_rate = 1.0 - stats.flip_rate
    # Wilson-style 95% one-sided lower bound on the mean of a bounded
    # variable (per D2: "Wilson 95% lower bound of the mean per-trade
    # return"): normal approximation with (n-1) variance and a Popoviciu
    # support bound, minus the z half-width.
    z = 1.96
    sd = _stdev(pnls)
    half = z * sd / math.sqrt(stats.n)
    stats.wilson_lo = stats.mean_pnl - half
    return stats


def grade(primary_oos: SegmentStats) -> str:
    """Apply pre-registered D2 kill criteria, in order."""
    if primary_oos.n < OOS_MIN_TRADES:
        return "INSUFFICIENT EVIDENCE (n<30): no claim either way"
    if primary_oos.mean_pnl <= 0:
        return "KILL: OOS mean net return <= 0"
    if primary_oos.flips / primary_oos.n >= FLIP_KILL_RATE:
        return "KILL: OOS flip rate >= 2%"
    if primary_oos.wilson_lo > 0:
        return "PASS: OOS mean > 0 with 95% lower bound > 0"
    return "FAIL: OOS mean > 0 but 95% lower bound <= 0 (not distinguishable from 0)"


def run_with_losers(store: object) -> dict:
    """Full version: carry trades on ANY token (winner or loser) priced >=
    threshold at lag. Winner tokens resolve 1, loser tokens resolve 0. This
    is the honest version of the strategy: we buy whatever is >= 0.97 and
    learn the realized flip rate from the loser tokens' fate."""
    markets = list(store.iter_resolved())
    histories: dict[str, list[tuple[int, float]]] = {}
    cursor = store.conn.execute(
        "SELECT token_id, ts, price FROM price_points ORDER BY token_id, ts"
    )
    current, series = None, []
    for token_id, ts, price in cursor.fetchall():
        if token_id != current:
            if current is not None and series:
                histories[current] = series
            current, series = token_id, []
        series.append((int(ts), float(price)))
    if current is not None and series:
        histories[current] = series

    dated = []
    for row in markets:
        tokens = json_loads_safe(row["clob_token_ids"])
        winner = row["winner_outcome_index"]
        end = _end_ts(row["end_date"])
        if winner is None or not tokens or end is None:
            continue
        dated.append((end, row))
    dated.sort(key=lambda x: x[0])
    split_idx = int(len(dated) * IS_FRACTION)
    split_end_ts = dated[split_idx - 1][0] if split_idx else 0

    by_config: dict[tuple[int, float], list[Trade]] = {}
    for end, row in dated:
        tokens = json_loads_safe(row["clob_token_ids"])
        winner = int(row["winner_outcome_index"])
        for lag in LAGS:
            cutoff = end - lag * DAY
            for i, token in enumerate(tokens):
                series = histories.get(str(token))
                if not series:
                    continue
                found = _last_point_at_or_before(series, cutoff)
                if found is None:
                    continue
                point_ts, price = found
                if (cutoff - point_ts) / DAY > STALE_MAX_DAYS:
                    continue
                if price < THRESHOLDS[0] or price >= 1.0:
                    continue
                fill = price + HALF_SPREAD
                if fill >= 1.0:
                    continue
                trade = Trade(
                    market_id=row["market_id"],
                    lag_days=lag,
                    threshold=THRESHOLDS[0],
                    p=price,
                    fill=fill,
                    fee=_fee(fill),
                    label=1 if i == winner else 0,
                    end_ts=end,
                )
                by_config.setdefault((lag, THRESHOLDS[0]), []).append(trade)
    return {"by_config": by_config, "split_end_ts": split_end_ts, "n_dated": len(dated)}


def report(result: dict) -> str:
    lines = ["# H-B1 decided-market carry — backtest (cost-aware)", ""]
    lines.append(
        f"cost model: fill = price + {HALF_SPREAD} + fee {FEE_RATE}*min(p,1-p) "
        f"(pre-registered D2) | split: IS 70% / OOS 30% by end_date "
        f"(split_ts={result['split_end_ts']})"
    )
    lines.append("")
    lines.append("| lag | seg | n | mean pnl/share | flips | flip rate | win rate | wilson lo |")
    lines.append("|---|---|---|---|---|---|---|---|")
    primary = None
    for (lag, _thr), trades in sorted(result["by_config"].items(), reverse=True):
        for name, seg in (("IS", [t for t in trades if t.end_ts <= result["split_end_ts"]]),
                          ("OOS", [t for t in trades if t.end_ts > result["split_end_ts"]])):
            st = _segment(seg)
            lines.append(
                f"| {lag}d | {name} | {st.n} | {st.mean_pnl:+.5f} | {st.flips} "
                f"| {st.flip_rate:.3f} | {st.win_rate:.3f} | {st.wilson_lo:+.5f} |"
            )
            if lag == 7 and name == "OOS":
                primary = st
    lines.append("")
    if primary is not None:
        lines.append(
            f"**PRIMARY (lag=7d, p>=0.97) OOS: n={primary.n}, "
            f"mean={primary.mean_pnl:+.5f}, flips={primary.flips} "
            f"({primary.flip_rate:.3f}), wilson_lo={primary.wilson_lo:+.5f}**"
        )
        lines.append("")
        lines.append(f"## VERDICT: {grade(primary)}")
    else:
        lines.append("## VERDICT: INSUFFICIENT EVIDENCE (no OOS trades)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="H-B1 backtest")
    parser.add_argument("--db", default="data/nexus_research.db")
    parser.add_argument("--half-spread", type=float, default=None,
                        help="override fill cost (D4 re-grade uses measured 0.00825)")
    parser.add_argument("--fee-rate", type=float, default=None)
    args = parser.parse_args(argv)
    if args.half_spread is not None:
        set_costs(args.half_spread, args.fee_rate if args.fee_rate is not None else FEE_RATE)
        print(f"cost override: half_spread={HALF_SPREAD} fee_rate={FEE_RATE}")
    from nexus.research.store import Store

    store = Store(args.db)
    try:
        result = run_with_losers(store)
    finally:
        store.close()
    print(report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
