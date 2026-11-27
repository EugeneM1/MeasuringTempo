"""Daily cross-venue snapshot: the most-traded open markets on each venue.

The collector (tempo/collector.py) records tick-level data for a hand-picked
watchlist. This is the opposite trade-off: once a day, one row per market for
the top markets by 24h volume on Kalshi and Polymarket. It needs no API key
(both endpoints are public) and runs unattended in GitHub Actions
(.github/workflows/daily-snapshot.yml), which commits the CSV to snapshots/.

Why it is worth having: research question 3 (calibration) needs a price for
many markets at a known time BEFORE they resolve. That history cannot be
backfilled cheaply, so it gets recorded from day one.

Run from the repo root:

    python -m tempo.snapshot

Fail-loud like the rest of the project: if either venue errors or returns
nothing, the run exits non-zero and writes no file.
"""

import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

TOP_N = 200                # rows kept per venue
POLYMARKET_PAGE = 100      # Gamma page size
KALSHI_PAGE = 200          # Kalshi events page size (API maximum)
KALSHI_MAX_PAGES = 40      # safety cap: Kalshi cannot sort by volume server-side,
                           # so we page through open events and rank locally
TIMEOUT_S = 30
RETRIES = 3

COLUMNS = [
    "snapshot_ts",   # UTC time this run started
    "venue",         # kalshi | polymarket
    "market_id",     # Kalshi ticker / Polymarket conditionId
    "event_id",      # Kalshi event_ticker / Polymarket slug
    "title",
    "yes_outcome",   # what a YES share pays out on
    "yes_bid",       # all prices are dollars per $1 payout, i.e. probabilities
    "yes_ask",
    "last_price",
    "volume_24h",    # Kalshi: contracts. Polymarket: USD. Not comparable across venues.
    "volume_total",
    "close_time",
]


def get_json(url: str, params: dict) -> object:
    """GET with a few retries; raises on the final failure."""
    for attempt in range(1, RETRIES + 1):
        try:
            resp = httpx.get(url, params=params, timeout=TIMEOUT_S)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            if attempt == RETRIES:
                raise
            time.sleep(5 * attempt)


def to_float(value) -> float | None:
    """Venues send numbers as numbers, strings, or nothing at all."""
    if value is None or value == "":
        return None
    return float(value)


def as_list(value) -> list:
    """Gamma sends some lists as JSON-encoded strings ('["Yes", "No"]')."""
    if isinstance(value, str):
        return json.loads(value)
    return value or []


def polymarket_row(market: dict, snapshot_ts: str) -> dict:
    """One Gamma market dict -> one CSV row. YES = the first listed outcome."""
    outcomes = as_list(market.get("outcomes"))
    return {
        "snapshot_ts": snapshot_ts,
        "venue": "polymarket",
        "market_id": market["conditionId"],
        "event_id": market.get("slug", ""),
        "title": market.get("question", ""),
        "yes_outcome": outcomes[0] if outcomes else "",
        "yes_bid": to_float(market.get("bestBid")),
        "yes_ask": to_float(market.get("bestAsk")),
        "last_price": to_float(market.get("lastTradePrice")),
        "volume_24h": to_float(market.get("volume24hr")),
        "volume_total": to_float(market.get("volumeNum")),
        "close_time": market.get("endDate", ""),
    }


def kalshi_row(market: dict, snapshot_ts: str) -> dict:
    """One Kalshi market dict -> one CSV row."""
    return {
        "snapshot_ts": snapshot_ts,
        "venue": "kalshi",
        "market_id": market["ticker"],
        "event_id": market.get("event_ticker", ""),
        "title": market.get("title", ""),
        "yes_outcome": market.get("yes_sub_title", ""),
        "yes_bid": to_float(market.get("yes_bid_dollars")),
        "yes_ask": to_float(market.get("yes_ask_dollars")),
        "last_price": to_float(market.get("last_price_dollars")),
        "volume_24h": to_float(market.get("volume_24h_fp")),
        "volume_total": to_float(market.get("volume_fp")),
        "close_time": market.get("close_time", ""),
    }


def top_by_volume(rows: list[dict], n: int) -> list[dict]:
    """The n rows with the highest 24h volume (missing volume counts as 0)."""
    return sorted(rows, key=lambda r: r["volume_24h"] or 0.0, reverse=True)[:n]


def fetch_polymarket(snapshot_ts: str, n: int = TOP_N) -> list[dict]:
    """Top-n open Polymarket markets. Gamma sorts by volume for us."""
    rows: list[dict] = []
    offset = 0
    while len(rows) < n:
        page = get_json(
            f"{GAMMA_BASE}/markets",
            {
                "closed": "false",
                "order": "volume24hr",
                "ascending": "false",
                "limit": POLYMARKET_PAGE,
                "offset": offset,
            },
        )
        if not page:
            break
        rows.extend(polymarket_row(m, snapshot_ts) for m in page if m.get("conditionId"))
        offset += POLYMARKET_PAGE
    return top_by_volume(rows, n)


def fetch_kalshi(snapshot_ts: str, n: int = TOP_N) -> list[dict]:
    """Top-n open Kalshi markets, ranked locally after paging through events."""
    rows: list[dict] = []
    cursor = None
    for _ in range(KALSHI_MAX_PAGES):
        params = {"status": "open", "limit": KALSHI_PAGE, "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        data = get_json(f"{KALSHI_BASE}/events", params)
        for event in data.get("events", []):
            for market in event.get("markets") or []:
                if market.get("status") == "active":
                    rows.append(kalshi_row(market, snapshot_ts))
        cursor = data.get("cursor")
        if not cursor:
            break
    return top_by_volume(rows, n)


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main(out_dir: str = "snapshots") -> None:
    now = datetime.now(timezone.utc)
    snapshot_ts = now.isoformat(timespec="seconds")

    kalshi = fetch_kalshi(snapshot_ts)
    polymarket = fetch_polymarket(snapshot_ts)
    if not kalshi or not polymarket:
        sys.exit(f"empty venue: kalshi={len(kalshi)} polymarket={len(polymarket)} rows — not writing")

    path = Path(out_dir) / f"{now:%Y}" / f"{now:%Y-%m-%d}.csv"
    write_csv(kalshi + polymarket, path)
    print(f"wrote {path}: {len(kalshi)} kalshi + {len(polymarket)} polymarket rows")


if __name__ == "__main__":
    main(*sys.argv[1:2])
