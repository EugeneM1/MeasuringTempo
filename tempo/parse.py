"""Parsers: one raw recorded message -> zero or more normalized table rows.

This is the only place venue message shapes are interpreted. Each parser
takes one JSONL line (dict, including our recv_ts) and returns a list of
(table, row) pairs matching schema.sql. Rules:

- Core fields are STRICT (a ticker message without a price is a bug worth
  seeing), optional fields use .get() — venues add fields without notice.
- Message types we don't load (subscription acks, venue errors, unknown
  types) return [] — they stay in the raw JSONL, which remains the source
  of truth. The loader counts skips so silently-growing skip rates are
  visible.
- Prices are kept as venue-sent decimal strings; Postgres NUMERIC parses
  them directly, so nothing round-trips through float.
- Polymarket empty-side sentinels: price_change reports best_bid="0" /
  best_ask="1" when a side is empty (observed Phase 0); those are stored
  as NULL, matching how an empty book snapshot side is stored. The raw
  message keeps the sentinel.

Field-shape provenance: Kalshi asyncapi.yaml tickerPayload + observed
frames; Polymarket realtime docs + Phase 0 recordings. If a venue changes
shape, parsers crash or skip loudly and the raw data is still on disk.
"""

from datetime import datetime, timezone
from typing import Any

Row = tuple[str, dict[str, Any]]


def _ms_to_ts(ms: int | str | None) -> datetime | None:
    """Venue epoch-milliseconds (int or string) -> aware datetime."""
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)


def _null_if(value: str | None, *sentinels: str) -> str | None:
    return None if value is None or value in sentinels else value


# ---------------------------------------------------------------- Kalshi ---

def parse_kalshi(message: dict) -> list[Row]:
    kind = message.get("type")
    recv_ts = message["recv_ts"]

    if kind == "ticker":
        msg = message["msg"]
        return [("quotes", {
            "venue": "kalshi",
            "market_id": msg["market_ticker"],
            "recv_ts": recv_ts,
            "venue_ts": _ms_to_ts(msg.get("ts_ms") or message.get("sending_ts_ms")),
            "best_bid": msg.get("yes_bid_dollars"),
            "best_ask": msg.get("yes_ask_dollars"),
            "bid_size": msg.get("yes_bid_size_fp"),
            "ask_size": msg.get("yes_ask_size_fp"),
            "raw": message,
        })]

    if kind == "trade":
        msg = message["msg"]
        return [("trades", {
            "venue": "kalshi",
            "market_id": msg["market_ticker"],
            "recv_ts": recv_ts,
            "venue_ts": _ms_to_ts(msg.get("ts_ms") or message.get("sending_ts_ms")),
            # dollars-string price for the YES side; older shapes send cents
            # ints under "yes_price" — accept either, prefer dollars.
            "price": msg.get("yes_price_dollars") or _cents_to_dollars(msg.get("yes_price")),
            "size": msg.get("count_fp") or msg.get("count"),
            "aggressor": msg.get("taker_side"),
            "raw": message,
        })]

    # subscribed / ok / error / lifecycle: not loaded, raw JSONL keeps them.
    return []


def _cents_to_dollars(cents: int | None) -> str | None:
    return None if cents is None else f"{cents / 100:.4f}"


# ------------------------------------------------------------ Polymarket ---

def parse_polymarket(message: dict) -> list[Row]:
    kind = message.get("event_type")
    recv_ts = message["recv_ts"]
    venue_ts = _ms_to_ts(message.get("timestamp"))

    if kind == "book":
        bids = [[lvl["price"], lvl["size"]] for lvl in message["bids"]]
        asks = [[lvl["price"], lvl["size"]] for lvl in message["asks"]]
        # Best-first for the books table; venue ordering is not trusted.
        bids.sort(key=lambda l: float(l[0]), reverse=True)
        asks.sort(key=lambda l: float(l[0]))
        rows: list[Row] = [("books", {
            "venue": "polymarket",
            "market_id": message["asset_id"],
            "recv_ts": recv_ts,
            "venue_ts": venue_ts,
            "bids": bids,
            "asks": asks,
            "raw": message,
        })]
        # A snapshot also yields a top-of-book quote row, so the quotes
        # table alone reconstructs best bid/ask without touching books.
        rows.append(("quotes", {
            "venue": "polymarket",
            "market_id": message["asset_id"],
            "recv_ts": recv_ts,
            "venue_ts": venue_ts,
            "best_bid": bids[0][0] if bids else None,
            "best_ask": asks[0][0] if asks else None,
            "bid_size": bids[0][1] if bids else None,
            "ask_size": asks[0][1] if asks else None,
            "raw": message,
        }))
        return rows

    if kind == "price_change":
        # One event carries one entry per affected token (subscribed token
        # AND its complement). Load them all: the complement's quotes are
        # real data for the other side of the same market.
        return [("quotes", {
            "venue": "polymarket",
            "market_id": pc["asset_id"],
            "recv_ts": recv_ts,
            "venue_ts": venue_ts,
            "best_bid": _null_if(pc.get("best_bid"), "0"),
            "best_ask": _null_if(pc.get("best_ask"), "1"),
            "bid_size": None,   # price_change carries level size, not BBO size
            "ask_size": None,
            "raw": message,
        }) for pc in message["price_changes"]]

    if kind == "last_trade_price":
        return [("trades", {
            "venue": "polymarket",
            "market_id": message["asset_id"],
            "recv_ts": recv_ts,
            "venue_ts": venue_ts,
            "price": message["price"],
            "size": message.get("size"),
            "aggressor": message.get("side"),
            "raw": message,
        })]

    # tick_size_change, lifecycle, anything new: skipped, raw keeps it.
    return []


PARSERS = {
    "kalshi_ws": parse_kalshi,
    "polymarket_ws": parse_polymarket,
}
