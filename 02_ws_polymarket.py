"""02_ws_polymarket.py — Phase 0: first contact with Polymarket's market WebSocket.

Design spec:
- Raw venue data untouched. Every event is written to JSONL exactly as received;
  the only addition is recv_ts, OUR receipt time (the venue's own "timestamp"
  field is kept as-is — the gap between the two is what this repo measures).
- Error policy: FAIL LOUD, same as 01_explore.py. No try/except, no reconnect
  (Phase 1). A dropped connection, an unknown event_type, or a missing field
  crashes with full detail. The raw event is written BEFORE it is parsed, so
  whatever caused a crash is already on disk.
- Public market channel only: no auth, no signing.
- Config: URLs and intervals as constants. Nothing here is secret.

Run:
    python 02_ws_polymarket.py TOKEN_ID [TOKEN_ID ...]
    (stop with Ctrl-C; find token ids with 01_explore.py)
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import websockets

WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PING_INTERVAL_S = 10
RAW_DIR = Path("data/raw")


def best_bid_ask(book: dict) -> tuple[str | None, str | None]:
    """Best bid and ask from a book snapshot; None for an empty side.

    Prices are decimal strings. Compare as floats but return the venue's
    string, and don't trust the venue's level ordering.
    """
    bids = [lvl["price"] for lvl in book["bids"]]
    asks = [lvl["price"] for lvl in book["asks"]]
    return (max(bids, key=float) if bids else None, min(asks, key=float) if asks else None)


def describe(event: dict) -> list[tuple[str, str]]:
    """(token_id, summary) for each printable line in one event.

    A price_change event carries one entry per affected token, so it can
    produce several lines; book and last_trade_price always produce one.
    """
    kind = event["event_type"]
    if kind == "book":
        bid, ask = best_bid_ask(event)
        return [(event["asset_id"], f"bid={bid} ask={ask}")]
    if kind == "price_change":
        return [
            (pc["asset_id"], f"bid={pc['best_bid']} ask={pc['best_ask']}  ({pc['side']} {pc['size']} @ {pc['price']})")
            for pc in event["price_changes"]
        ]
    if kind == "last_trade_price":
        return [(event["asset_id"], f"trade={event['price']}  ({event['side']} {event['size']})")]
    raise ValueError(f"unexpected event_type {kind!r}: {event}")


def record(event: dict, recv: datetime) -> None:
    """Append one raw event to today's JSONL file, tagged with recv_ts."""
    if "recv_ts" in event:
        raise ValueError(f"venue event already has a recv_ts field: {event}")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    # File is named by the UTC date of receipt, so a run that crosses midnight
    # rolls over to a new file on its own.
    path = RAW_DIR / f"polymarket_ws_{recv:%Y-%m-%d}.jsonl"
    with path.open("a") as f:
        f.write(json.dumps({**event, "recv_ts": recv.isoformat(timespec="milliseconds")}) + "\n")


async def keepalive(ws) -> None:
    """Send the application-level text PING the venue requires.

    This is a normal text message containing "PING", not a WebSocket protocol
    ping frame (the websockets library sends those on its own, separately).
    The server answers with a text "PONG".
    """
    while True:
        await asyncio.sleep(PING_INTERVAL_S)
        await ws.send("PING")


async def receive(ws) -> None:
    async for frame in ws:
        recv = datetime.now(timezone.utc)  # stamp first, before any parsing
        if frame == "PONG":
            continue
        payload = json.loads(frame)
        # The initial book snapshots arrive as a JSON array of events in one
        # frame; later updates arrive as a single object. Same handling.
        events = payload if isinstance(payload, list) else [payload]
        for event in events:
            record(event, recv)
            for token_id, summary in describe(event):
                print(f"{recv:%H:%M:%S}.{recv.microsecond // 1000:03d}Z  {event['event_type']:<16}  {token_id}  {summary}", flush=True)
    # The loop only ends if the server closes cleanly. With no reconnect logic
    # that is still the end of the recording, so say so loudly.
    raise RuntimeError(f"server closed the connection: code={ws.close_code} reason={ws.close_reason!r}")


async def main(token_ids: list[str]) -> None:
    async with websockets.connect(WS_MARKET_URL) as ws:
        await ws.send(json.dumps({"assets_ids": token_ids, "type": "market"}))
        # TaskGroup: if either task crashes, the other is cancelled and the
        # exception surfaces — a dead pinger can't leave a zombie receiver.
        async with asyncio.TaskGroup() as tg:
            tg.create_task(keepalive(ws))
            tg.create_task(receive(ws))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1:]))
