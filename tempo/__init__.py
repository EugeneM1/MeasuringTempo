"""tempo — the MeasuringTempo collector package (Phase 1).

The numbered scripts (01-04) were Phase 0: standalone, fail-loud exploration.
This package is the always-on collector that replaces them:

    tempo/config.py      what to watch (loaded from watchlist.json)
    tempo/signing.py     Kalshi request signing (Ed25519 / RSA-PSS)
    tempo/recorder.py    raw JSONL writer, date-rolled, recv_ts-stamped
    tempo/kalshi.py      one authenticated Kalshi WebSocket session
    tempo/polymarket.py  one Polymarket market WebSocket session
    tempo/collector.py   the supervisor: runs every stream forever (reconnect)

Run the collector from the repo root:

    python -m tempo.collector
"""
