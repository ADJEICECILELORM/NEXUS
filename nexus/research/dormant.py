"""H-A2: depth-weighted dormant-tail measurement in negRisk event families.

Measurement only — NOT a strategy claim (docs/DECISIONS.md D1; F1/F4 context).
For open negRisk events, snapshot every outcome's book and classify:

- live:         two-sided, both sides >= $500 depth
- dormant:      two-sided but at least one side < $500 depth  <- maker zone
- one-sided:    only bids or only asks resting
- no-orderbook: CLOB 404 "No orderbook exists" (the truest dormant state)
- dead/error:   empty book / real client failure

    python -m nexus.research.dormant --max-events 30
"""
from __future__ import annotations

import argparse
import json
import statistics
import time

from nexus.polymarket.clob import ClobPublicClient
from nexus.polymarket.gamma import GammaClient
from nexus.research.negrisk import _EVENT_PARAMS, _PAGE_CAP, event_markets, token_id

DEEP_USD = 500.0  # a side with less resting USD than this is "thin"


def _median(vals: list[float]) -> float | None:
    return statistics.median(vals) if vals else None


def _percentile(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, round(q * (len(s) - 1))))]


def classify(bid_usd: float, ask_usd: float, spread: float | None) -> str:
    if bid_usd <= 0 and ask_usd <= 0:
        return "dead"
    if bid_usd <= 0 or ask_usd <= 0:
        return "one-sided"
    if bid_usd < DEEP_USD or ask_usd < DEEP_USD:
        return "dormant"
    return "live"


def measure_event(event: dict, clob: ClobPublicClient, sleep: float = 0.1) -> dict:
    markets = event_markets(event)
    tokens = [t for t in (token_id(m) for m in markets) if t]
    out = {
        "event_slug": event.get("slug"),
        "title": event.get("title"),
        "volume_num": event.get("volume") or event.get("volumeNum"),
        "outcomes": [],
    }
    for token in tokens:
        try:
            book = clob.get_book(token)
        except Exception as e:
            # CLOB answers HTTP 404 "No orderbook exists" for outcomes that
            # never attracted any resting order — that IS the dormant tail,
            # not a client failure. Classify it; only real errors stay errors.
            status = "no-orderbook" if "404" in str(e) else "error"
            out["outcomes"].append({"token_id": token, "status": status})
            continue
        bid_usd = sum(l.price * l.size for l in book.bids)
        ask_usd = sum(l.price * l.size for l in book.asks)
        spread = (book.best_ask - book.best_bid) if (book.bids and book.asks) else None
        out["outcomes"].append({
            "token_id": token,
            "status": classify(bid_usd, ask_usd, spread),
            "best_bid": book.best_bid,
            "best_ask": book.best_ask,
            "spread": spread,
            "bid_usd": round(bid_usd, 2),
            "ask_usd": round(ask_usd, 2),
        })
        time.sleep(sleep)
    return out


def scan_events(gamma: GammaClient, clob: ClobPublicClient, max_events: int,
                sleep: float = 0.1) -> list[dict]:
    events_out: list[dict] = []
    seen = 0
    for page_i in range(_PAGE_CAP):
        page = gamma.http.get("/events", {**_EVENT_PARAMS, "offset": page_i * 100})
        if not isinstance(page, list) or not page:
            break
        for event in page:
            if seen >= max_events:
                return events_out
            if not event.get("negRisk"):
                continue
            seen += 1
            res = measure_event(event, clob, sleep)
            n_tok = len(res["outcomes"])
            print(f"  event {seen}: {res['event_slug']} ({n_tok} outcomes)")
            events_out.append(res)
    return events_out


def aggregate(events: list[dict]) -> dict:
    statuses: dict[str, int] = {}
    dormant_spreads: list[float] = []
    dormant_bid: list[float] = []
    dormant_ask: list[float] = []
    live_spreads: list[float] = []
    fam_dormant_frac: list[float] = []
    for ev in events:
        outs = ev["outcomes"]
        n = len(outs)
        if n == 0:
            continue
        n_dormant = 0
        for o in outs:
            statuses[o["status"]] = statuses.get(o["status"], 0) + 1
            if o["status"] == "dormant":
                n_dormant += 1
                if o.get("spread") is not None:
                    dormant_spreads.append(o["spread"])
                dormant_bid.append(o.get("bid_usd", 0.0))
                dormant_ask.append(o.get("ask_usd", 0.0))
            elif o["status"] == "live" and o.get("spread") is not None:
                live_spreads.append(o["spread"])
        fam_dormant_frac.append(n_dormant / n)
    return {
        "n_events": len(events),
        "outcome_statuses": statuses,
        "dormant_spread": {"median": _median(dormant_spreads), "p90": _percentile(dormant_spreads, 0.9)},
        "dormant_bid_usd": {"median": _median(dormant_bid)},
        "dormant_ask_usd": {"median": _median(dormant_ask)},
        "live_spread": {"median": _median(live_spreads)},
        "family_dormant_fraction": {"median": _median(fam_dormant_frac)},
    }


def report(st: dict) -> str:
    lines = ["# H-A2 dormant-tail measurement (negRisk families)", ""]
    lines.append(f"events measured: {st['n_events']} | outcome books: {sum(st['outcome_statuses'].values())}")
    lines.append("")
    lines.append("| status | count |")
    lines.append("|---|---|")
    for k, v in sorted(st["outcome_statuses"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {v} |")
    lines.append("")

    def fmt(v):
        return f"{v:.4f}" if isinstance(v, float) else "—"

    ds = st["dormant_spread"]
    lines.append("| metric (dormant outcomes) | median | p90 |")
    lines.append("|---|---|---|")
    lines.append(f"| available spread (maker gross capture/round trip) | {fmt(ds['median'])} | {fmt(ds['p90'])} |")
    lines.append(f"| bid-side resting depth USD | {fmt(st['dormant_bid_usd']['median'])} | — |")
    lines.append(f"| ask-side resting depth USD | {fmt(st['dormant_ask_usd']['median'])} | — |")
    lines.append("")
    ls = st["live_spread"]
    lines.append(f"live-outcome median spread: {fmt(ls['median'])}  |  "
                 f"median family dormant fraction: "
                 f"{fmt(st['family_dormant_fraction']['median'])}")
    lines.append("")
    lines.append("Measurement only — adverse-selection risk is NOT measured here.")
    lines.append("No strategy claim; the maker zone exists, its economics under")
    lines.append("news shocks remain unquantified until the paper phase.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="H-A2 dormant-tail measurement")
    parser.add_argument("--max-events", type=int, default=40)
    parser.add_argument("--sleep", type=float, default=0.1)
    parser.add_argument("--out", default="data/dormant_snapshot.json")
    args = parser.parse_args(argv)
    gamma = GammaClient()
    clob = ClobPublicClient()
    events = scan_events(gamma, clob, args.max_events, args.sleep)
    st = aggregate(events)
    rep = report(st)
    print(rep)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"stats": st, "events": events}, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
