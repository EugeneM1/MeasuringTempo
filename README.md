# MeasuringTempo

How fast do prediction markets learn? A real-time, tick-level data pipeline
and research project measuring price discovery, news-reaction latency, and
calibration across Kalshi, Polymarket, and sportsbooks.

Extends Angelini & De Angelis (2026), "When Do Markets Fully Process Public
Information? Evidence from Real-Time Prediction Markets"
([arXiv:2606.07811](https://arxiv.org/abs/2606.07811)), from a single venue
to a cross-venue setting.

## Research questions

1. Which venue leads price discovery, and by how many seconds?
2. How long until a timestamped news event is fully priced in?
3. Are market prices well-calibrated probabilities?
4. Does any mispricing survive spreads, fees, and order delays?

## Layout

| Path | What |
| --- | --- |
| 01_explore.py – 04_tape.py | Phase 0: standalone, fail-loud exploration scripts (REST, WebSockets, the two-venue live tape) |
| tempo/ | Phase 1: the always-on collector package (python -m tempo.collector) |
| schema.sql | Postgres tables the raw JSONL loads into |
| watchlist.json | which markets the collector records |
| tests/ | pytest suite for everything testable offline |

## Setup

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env   # then fill in KALSHI_KEY_ID / KALSHI_KEY_PATH
pytest
```

Market data on both venues is public; Kalshi's WebSocket additionally needs
an API key from a verified account (data access only — this project places
no orders).

## Status

Phase 1 in progress: collector package scaffolded; reconnect supervisor and
Postgres loader in flight. Raw order-book data cannot be backfilled, so the
collector ships before any analysis.
