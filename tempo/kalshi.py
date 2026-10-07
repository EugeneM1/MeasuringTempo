"""One authenticated Kalshi WebSocket session: connect, subscribe, record.

run_once() runs a single session until the connection ends, then returns
normally (clean close) or raises (anything else). It deliberately does NOT
reconnect — staying alive across failures is the supervisor's job
(collector.py), so each layer has one job.

Message handling is record-first: every frame is written to the Recorder
before any parsing, so whatever crashes the parser is already on disk.
Unlike Phase 0, a venue "error" message or unknown type logs a line and
continues — in an unattended collector, one bad message must not stop the
recording of fifty healthy markets. The raw message is on disk either way.
"""

import json
import logging
import time

import websockets

from .config import Config
from .recorder import Recorder
from .signing import auth_headers

WS_HOST = "external-api-ws.kalshi.com"
WS_PATH = "/trade-api/ws/v2"
WS_URL = f"wss://{WS_HOST}{WS_PATH}"

log = logging.getLogger("tempo.kalshi")


async def run_once(config: Config, recorder: Recorder) -> None:
    """One connect-subscribe-record session; returns on clean close."""
    headers = auth_headers(
        config.kalshi_key_id, config.kalshi_key_path, int(time.time() * 1000), "GET", WS_PATH
    )
    try:
        ws_cm = await websockets.connect(WS_URL, additional_headers=headers)
    except websockets.exceptions.InvalidStatus as exc:
        resp = exc.response
        raise RuntimeError(
            f"kalshi handshake rejected: HTTP {resp.status_code}  "
            f"body={bytes(resp.body).decode(errors='replace')}"
        ) from exc
    async with ws_cm as ws:
        await ws.send(json.dumps({
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker", "trade"],
                "market_tickers": config.kalshi_tickers,
                "send_initial_snapshot": True,
            },
        }))
        log.info("kalshi: subscribed to %d markets", len(config.kalshi_tickers))
        async for frame in ws:
            message = json.loads(frame)
            recorder.write(message)
            if message.get("type") == "error":
                log.error("kalshi venue error (recorded): %s", message)
        log.info("kalshi: server closed cleanly: code=%s", ws.close_code)
