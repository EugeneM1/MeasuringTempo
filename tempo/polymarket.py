"""One Polymarket market WebSocket session: connect, subscribe, record.

Same shape as kalshi.run_once: a single session that returns on clean close
and raises on anything else; reconnecting is the supervisor's job. Public
channel, no auth. The venue requires an application-level text "PING" every
10 seconds (its "PONG" replies are not recorded — they're keepalive noise,
not market data).
"""

import asyncio
import json
import logging

import websockets

from .config import Config
from .recorder import Recorder

WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PING_INTERVAL_S = 10

log = logging.getLogger("tempo.polymarket")


async def run_once(config: Config, recorder: Recorder) -> None:
    """One connect-subscribe-record session; returns on clean close."""
    async with websockets.connect(WS_MARKET_URL) as ws:
        await ws.send(json.dumps({"assets_ids": config.polymarket_tokens, "type": "market"}))
        log.info("polymarket: subscribed to %d tokens", len(config.polymarket_tokens))

        async def keepalive() -> None:
            while True:
                await asyncio.sleep(PING_INTERVAL_S)
                await ws.send("PING")

        # keepalive runs beside the receive loop below. It's a plain task
        # (not a TaskGroup) because it never finishes on its own: the receive
        # loop decides when the session is over, then the finally cancels the
        # pinger. If keepalive itself dies (send on a dead socket), the
        # receive loop sees the same dead socket and raises — nothing hangs.
        keeper = asyncio.create_task(keepalive())
        try:
            async for frame in ws:
                if frame == "PONG":
                    continue
                payload = json.loads(frame)
                # Initial snapshots arrive as one array frame; updates as
                # single objects. One recorded line per event either way.
                for event in payload if isinstance(payload, list) else [payload]:
                    recorder.write(event)
            log.info("polymarket: server closed cleanly: code=%s", ws.close_code)
        finally:
            keeper.cancel()
