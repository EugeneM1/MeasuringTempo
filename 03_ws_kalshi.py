"""03_ws_kalshi.py — Phase 0: first contact with Kalshi's market WebSocket.

Design spec:
- Raw venue data untouched. Every message is written to JSONL exactly as
  received; the only addition is recv_ts, OUR receipt time (the venue's own
  "sending_ts_ms" and "ts_ms" fields are kept as-is — the gap between those and
  recv_ts is what this repo measures).
- Error policy: FAIL LOUD, same as 01_explore.py and 02_ws_polymarket.py. No
  reconnect (Phase 1). A dropped connection, a venue "error" message, an unknown
  message type, or a missing field crashes with full detail. The raw message is
  written BEFORE it is parsed, so whatever caused a crash is already on disk.
  The one except clause in this file only ADDS detail: a rejected handshake is
  re-raised with the HTTP body, which the library's own exception leaves out.
- Auth: unlike Polymarket's market channel, Kalshi requires three signed
  headers on the WebSocket handshake even for public data like ticker. The
  signing itself lives in sign_request() — see its docstring for the spec.
- Config: URL and path as constants. The key id and private key path ARE
  secret-adjacent, so they come from .env (which is gitignored, as is *.pem):
      KALSHI_KEY_ID=<the Key ID shown when the API key was created>
      KALSHI_KEY_PATH=<path to the private key PEM file>
- Keep-alive: nothing to do. Kalshi sends WebSocket protocol Ping frames every
  10s and the websockets library answers them with Pong on its own. (Polymarket
  needed an application-level text "PING" task; Kalshi does not.)

Sources (checked 2026-10-07):
- URL, channels, message shapes: https://docs.kalshi.com/asyncapi.yaml
- Headers and signing: https://docs.kalshi.com/getting_started/api_keys
- WebSocket handshake signing: https://docs.kalshi.com/getting_started/quick_start_websockets

Run:
    python 03_ws_kalshi.py MARKET_TICKER [MARKET_TICKER ...]
    (stop with Ctrl-C; find market tickers with 01_explore.py)
"""

import asyncio
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from dotenv import load_dotenv

# asyncapi.yaml servers.production: host external-api-ws.kalshi.com, pathname
# /trade-api/ws/v2. The older wss://api.elections.kalshi.com host that matches
# 01_explore.py's REST base is still supported; the signed path is the same.
WS_HOST = "external-api-ws.kalshi.com"
WS_PATH = "/trade-api/ws/v2"
WS_URL = f"wss://{WS_HOST}{WS_PATH}"
RAW_DIR = Path("data/raw")


def sign_request(private_key_path: str, timestamp_ms: int, method: str, path: str) -> str:
    """Sign one Kalshi request; return the KALSHI-ACCESS-SIGNATURE header value.

    Message to sign — three fields concatenated with NO separator, in this order:

        str(timestamp_ms) + method + path

    - timestamp_ms: Unix time in milliseconds, as decimal digits. Must be the
      very same value that is sent in the KALSHI-ACCESS-TIMESTAMP header.
    - method: the HTTP method in upper case. A WebSocket handshake is an HTTP
      GET, so for this script it is always "GET".
    - path: the request path from the API root, without scheme or host and
      without query string (drop "?" and everything after it). For this script
      it is always "/trade-api/ws/v2".

    Example: timestamp 1791380000000 for this script's handshake signs exactly

        1791380000000GET/trade-api/ws/v2

    Encode that string as UTF-8 bytes before signing.

    Key: the PEM file at private_key_path, unencrypted (no password). Load it
    with cryptography.hazmat.primitives.serialization.load_pem_private_key.
    Kalshi issues two key types and picks the verification algorithm from the
    registered public key, so sign with the scheme that matches the loaded key.
    The PEM header does not tell you which type it is (a PKCS#8 RSA key also
    starts "-----BEGIN PRIVATE KEY-----"); check the type of the loaded object.

    - Ed25519 key (Kalshi's default for new keys): plain Ed25519 per RFC 8032
      over the message bytes. No padding, no salt, and no separate hash step —
      do not pre-hash the message. The signature is always 64 bytes.
    - RSA key (2048-bit): RSA-PSS with SHA-256 as the message hash, MGF1 with
      SHA-256 as the mask function, and salt length equal to the digest length
      (32 bytes). The signature is 256 bytes. PKCS#1 v1.5 padding is rejected.

    Output: the raw signature bytes, standard base64 (with "=" padding, not the
    URL-safe alphabet), decoded to a str. 64 bytes of Ed25519 signature become
    88 base64 characters.

    Headers this feeds (all three go on the handshake; auth_headers() builds them):
        KALSHI-ACCESS-KEY        the Key ID (KALSHI_KEY_ID) — not signed, it
                                 tells Kalshi which public key to verify with
        KALSHI-ACCESS-TIMESTAMP  str(timestamp_ms), the same value signed here
        KALSHI-ACCESS-SIGNATURE  this function's return value

    Docs: https://docs.kalshi.com/getting_started/api_keys
    """
    key_bytes = Path(private_key_path).read_bytes()
    key = load_pem_private_key(key_bytes, password=None)
    message = (str(timestamp_ms) + method + path).encode("utf-8")
    if isinstance(key, ed25519.Ed25519PrivateKey):
        signature = key.sign(message)
    elif isinstance(key, rsa.RSAPrivateKey):
        signature = key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
            hashes.SHA256(),
        )
    else:
        raise TypeError(f"unsupported key type {type(key).__name__} — Kalshi issues Ed25519 or RSA keys")
    return base64.b64encode(signature).decode()


def auth_headers(key_id: str, private_key_path: str) -> dict[str, str]:
    """The three signed headers for the WebSocket handshake."""
    # One timestamp, used for both the signature and the header. If they
    # differ by even a millisecond the signature won't verify.
    timestamp_ms = int(time.time() * 1000)
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
        "KALSHI-ACCESS-SIGNATURE": sign_request(private_key_path, timestamp_ms, "GET", WS_PATH),
    }


def describe(message: dict) -> tuple[str, str]:
    """(market_ticker, summary) for one message's printable line.

    Prices are dollar strings ("0.4500"), printed as the venue sent them.
    """
    kind = message["type"]
    if kind == "ticker":
        msg = message["msg"]
        return (
            msg["market_ticker"],
            f"bid={msg['yes_bid_dollars']} ask={msg['yes_ask_dollars']}  "
            f"(last {msg['price_dollars']}, sizes {msg['yes_bid_size_fp']} x {msg['yes_ask_size_fp']})",
        )
    if kind == "subscribed":
        return ("-", f"channel={message['msg']['channel']} sid={message['msg']['sid']}")
    if kind == "ok":
        return ("-", json.dumps(message.get("msg")))
    if kind == "error":
        # e.g. {"id": 1, "type": "error", "msg": {"code": 8, "msg": "Unknown channel name"}}
        raise RuntimeError(f"venue error {message['msg']['code']}: {message['msg']['msg']}  ({message})")
    raise ValueError(f"unexpected message type {kind!r}: {message}")


def record(message: dict, recv: datetime) -> None:
    """Append one raw message to today's JSONL file, tagged with recv_ts."""
    if "recv_ts" in message:
        raise ValueError(f"venue message already has a recv_ts field: {message}")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    # File is named by the UTC date of receipt, so a run that crosses midnight
    # rolls over to a new file on its own.
    path = RAW_DIR / f"kalshi_ws_{recv:%Y-%m-%d}.jsonl"
    with path.open("a") as f:
        f.write(json.dumps({**message, "recv_ts": recv.isoformat(timespec="milliseconds")}) + "\n")


async def receive(ws) -> None:
    async for frame in ws:
        recv = datetime.now(timezone.utc)  # stamp first, before any parsing
        # Every Kalshi frame is one JSON object: {"type": ..., "msg": {...}},
        # plus "sid" on channel data and "id" on replies to our commands.
        message = json.loads(frame)
        record(message, recv)
        market_ticker, summary = describe(message)
        print(f"{recv:%H:%M:%S}.{recv.microsecond // 1000:03d}Z  {message['type']:<16}  {market_ticker}  {summary}", flush=True)
    # The loop only ends if the server closes cleanly. With no reconnect logic
    # that is still the end of the recording, so say so loudly.
    raise RuntimeError(f"server closed the connection: code={ws.close_code} reason={ws.close_reason!r}")


async def main(market_tickers: list[str]) -> None:
    load_dotenv()
    headers = auth_headers(os.environ["KALSHI_KEY_ID"], os.environ["KALSHI_KEY_PATH"])
    try:
        ws_cm = await websockets.connect(WS_URL, additional_headers=headers)
    except websockets.exceptions.InvalidStatus as exc:
        # Auth is checked during the handshake, so a bad key id, signature or
        # timestamp shows up here as HTTP 401, before any WebSocket exists.
        # The reason is in the response body, which the exception's own
        # message does not include.
        resp = exc.response
        raise RuntimeError(
            f"handshake rejected: HTTP {resp.status_code} {resp.reason_phrase}  body={bytes(resp.body).decode(errors='replace')}"
        ) from exc
    async with ws_cm as ws:
        # asyncapi.yaml subscribeCommandPayload: id, cmd and params.channels are
        # required. market_tickers (array) covers one market or many, so there
        # is no need to switch to market_ticker (string) for a single ticker.
        # send_initial_snapshot gives one ticker message per market right away;
        # without it a quiet market prints nothing until something changes.
        await ws.send(json.dumps({
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker"],
                "market_tickers": market_tickers,
                "send_initial_snapshot": True,
            },
        }))
        await receive(ws)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1:]))
