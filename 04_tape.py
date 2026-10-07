"""04_tape.py — Phase 0 milestone: one game, both venues, one merged live tape.

Runs the Kalshi ticker stream and the Polymarket market stream concurrently
(asyncio) for a single game and prints one merged tape: every quote update from
either venue, as implied probabilities, plus the live gap between the two
venues' midpoints in probability points. This is the project's thesis on one
screen — watch which venue moves first when something happens in the game.

Design spec:
- Raw venue data untouched: both streams keep logging to their own JSONL files
  in data/raw/, same convention as scripts 02 and 03 (recv_ts added, nothing
  else touched). The tape is presentation only.
- Error policy: FAIL LOUD. No reconnect (Phase 1). asyncio.TaskGroup means a
  crash in either stream cancels the other and surfaces — a dead feed can't
  leave a silently half-blind tape.
- Prices: Kalshi ticker gives yes_bid/yes_ask as dollar strings; Polymarket
  gives bid/ask per token. Both are probabilities already. Midpoint = (bid+ask)/2.
  The gap line only prints once both venues have produced a quote.
- This file imports from 02/03 via importlib because their names start with a
  digit (noted in Phase 0 review: the numbered-script convention dies in
  Phase 1, when this becomes a real package).

Run:
    python 04_tape.py KALSHI_TICKER POLYMARKET_TOKEN_ID
    (the pair must be the same game, same side — e.g. both the Yankees side;
     find a pair with 01_explore.py)
"""

import asyncio
import importlib.util
import json
import sys
from datetime import datetime, timezone


def load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pm = load("ws_polymarket", "02_ws_polymarket.py")
kx = load("ws_kalshi", "03_ws_kalshi.py")

# Latest midpoint per venue, updated by the stream tasks and read by the
# printer. Plain dict, no locks needed: asyncio is single-threaded, and each
# update is one assignment.
latest: dict[str, float] = {}


def now() -> datetime:
    return datetime.now(timezone.utc)


def tape_line(recv: datetime, venue: str, bid: str | None, ask: str | None) -> None:
    """Print one tape line and the cross-venue gap when both sides are known."""
    if bid is not None and ask is not None:
        latest[venue] = (float(bid) + float(ask)) / 2
    mid = latest.get(venue)
    gap = ""
    if "kalshi" in latest and "polymkt" in latest:
        points = (latest["kalshi"] - latest["polymkt"]) * 100
        gap = f"  gap={points:+.1f}pts"
    mid_s = f"{mid:.3f}" if mid is not None else "n/a"
    print(
        f"{recv:%H:%M:%S}.{recv.microsecond // 1000:03d}Z  {venue:<8}  "
        f"bid={bid or '-':<6} ask={ask or '-':<6} mid={mid_s}{gap}",
        flush=True,
    )


async def kalshi_stream(ticker: str) -> None:
    headers = kx.auth_headers(*_kalshi_creds())
    try:
        ws_cm = await kx.websockets.connect(kx.WS_URL, additional_headers=headers)
    except kx.websockets.exceptions.InvalidStatus as exc:
        resp = exc.response
        raise RuntimeError(
            f"kalshi handshake rejected: HTTP {resp.status_code}  body={bytes(resp.body).decode(errors='replace')}"
        ) from exc
    async with ws_cm as ws:
        await ws.send(json.dumps({
            "id": 1,
            "cmd": "subscribe",
            "params": {"channels": ["ticker"], "market_tickers": [ticker], "send_initial_snapshot": True},
        }))
        async for frame in ws:
            recv = now()
            message = json.loads(frame)
            kx.record(message, recv)
            if message["type"] == "ticker":
                msg = message["msg"]
                tape_line(recv, "kalshi", msg["yes_bid_dollars"], msg["yes_ask_dollars"])
            elif message["type"] == "error":
                raise RuntimeError(f"kalshi venue error: {message}")
            # "subscribed"/"ok" replies: recorded above, nothing to print
        raise RuntimeError(f"kalshi closed the connection: code={ws.close_code}")


async def polymarket_stream(token_id: str) -> None:
    async with pm.websockets.connect(pm.WS_MARKET_URL) as ws:
        await ws.send(json.dumps({"assets_ids": [token_id], "type": "market"}))

        async def keepalive() -> None:
            while True:
                await asyncio.sleep(pm.PING_INTERVAL_S)
                await ws.send("PING")

        async def receive() -> None:
            async for frame in ws:
                recv = now()
                if frame == "PONG":
                    continue
                payload = json.loads(frame)
                for event in payload if isinstance(payload, list) else [payload]:
                    pm.record(event, recv)
                    kind = event["event_type"]
                    if kind == "book":
                        bid, ask = pm.best_bid_ask(event)
                        tape_line(recv, "polymkt", bid, ask)
                    elif kind == "price_change":
                        # Only our subscribed token; the mirrored complement
                        # token in the same event is recorded but not printed.
                        for pc in event["price_changes"]:
                            if pc["asset_id"] == token_id:
                                tape_line(recv, "polymkt", pc["best_bid"], pc["best_ask"])
                    # last_trade_price moves no quotes; recorded, not printed
            raise RuntimeError(f"polymarket closed the connection: code={ws.close_code}")

        async with asyncio.TaskGroup() as tg:
            tg.create_task(keepalive())
            tg.create_task(receive())


def _kalshi_creds() -> tuple[str, str]:
    import os

    from dotenv import load_dotenv

    load_dotenv()
    return os.environ["KALSHI_KEY_ID"], os.environ["KALSHI_KEY_PATH"]


async def main(ticker: str, token_id: str) -> None:
    print(f"tape: kalshi {ticker}  vs  polymarket {token_id[:16]}…  (Ctrl-C to stop)")
    async with asyncio.TaskGroup() as tg:
        tg.create_task(kalshi_stream(ticker))
        tg.create_task(polymarket_stream(token_id))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
