-- MeasuringTempo Postgres schema (Phase 1).
-- Raw JSONL on disk stays the source of truth; these tables are the queryable
-- view, loaded by a batch job from data/raw/. Rebuildable at any time.
--
-- Design notes:
-- * Every row carries recv_ts (our NTP-synced receipt time) AND venue_ts (the
--   venue's own timestamp when it sends one). The difference is feed latency;
--   never mix the two in an analysis without saying which you're using.
-- * Prices are NUMERIC(6,4) probabilities in [0,1] on both venues' quotes.
--   Sizes keep each venue's native unit (contracts vs shares) — normalizing
--   units is an analysis-time decision, recorded here as-is.
-- * raw JSONB on every row: the full original message, so no field the
--   loader didn't extract is ever lost.
-- * If TimescaleDB is installed, each table ends with a commented
--   create_hypertable call — optional, plain Postgres works fine to start.

CREATE TABLE IF NOT EXISTS quotes (
    venue       text        NOT NULL,              -- 'kalshi' | 'polymarket'
    market_id   text        NOT NULL,              -- kalshi ticker | polymarket token_id
    recv_ts     timestamptz NOT NULL,
    venue_ts    timestamptz,
    best_bid    numeric(6,4),                      -- NULL = empty side
    best_ask    numeric(6,4),
    bid_size    numeric,
    ask_size    numeric,
    raw         jsonb       NOT NULL
);
CREATE INDEX IF NOT EXISTS quotes_market_time ON quotes (venue, market_id, recv_ts);
-- SELECT create_hypertable('quotes', 'recv_ts');

CREATE TABLE IF NOT EXISTS trades (
    venue       text        NOT NULL,
    market_id   text        NOT NULL,
    recv_ts     timestamptz NOT NULL,
    venue_ts    timestamptz,
    price       numeric(6,4) NOT NULL,
    size        numeric      NOT NULL,
    aggressor   text,                              -- taker side when the venue says
    raw         jsonb       NOT NULL
);
CREATE INDEX IF NOT EXISTS trades_market_time ON trades (venue, market_id, recv_ts);
-- SELECT create_hypertable('trades', 'recv_ts');

CREATE TABLE IF NOT EXISTS books (
    venue       text        NOT NULL,
    market_id   text        NOT NULL,
    recv_ts     timestamptz NOT NULL,
    venue_ts    timestamptz,
    bids        jsonb       NOT NULL,              -- [[price, size], ...] best-first
    asks        jsonb       NOT NULL,
    raw         jsonb       NOT NULL
);
CREATE INDEX IF NOT EXISTS books_market_time ON books (venue, market_id, recv_ts);
-- SELECT create_hypertable('books', 'recv_ts');

-- Timestamped real-world events that anchor event studies: plays, economic
-- releases, headlines. source = 'nba_api' | 'bls_schedule' | ...; event_ts is
-- when it happened in the world, recv_ts when we learned of it.
CREATE TABLE IF NOT EXISTS events (
    event_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source      text        NOT NULL,
    event_type  text        NOT NULL,              -- 'made_3pt', 'cpi_release', ...
    event_ts    timestamptz NOT NULL,
    recv_ts     timestamptz,
    game_or_series text,                           -- join key toward markets
    payload     jsonb       NOT NULL
);
CREATE INDEX IF NOT EXISTS events_time ON events (source, event_ts);

-- The matched universe: which identifiers on each venue are the same
-- real-world contract. Written by the Phase 2 matcher; match_method records
-- how the row got here ('manual', 'embedding+llm', ...) and confidence lets
-- analyses filter to high-certainty matches.
CREATE TABLE IF NOT EXISTS matched_markets (
    match_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    description     text    NOT NULL,              -- 'Yankees win ALDS Game 4, Oct 8 2026'
    kalshi_ticker   text,
    polymarket_token text,
    sportsbook_key  text,                          -- odds-api event+market key
    outcome_side    text    NOT NULL,              -- which side of the binary all ids refer to
    match_method    text    NOT NULL,
    confidence      numeric(3,2),
    verified_by_human boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kalshi_ticker, polymarket_token, outcome_side)
);
