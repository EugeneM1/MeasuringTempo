"""Parsers: realistic venue messages (shapes from Phase 0 recordings) -> rows."""

from tempo.parse import parse_kalshi, parse_polymarket

RECV = "2026-10-08T00:30:00.000+00:00"


def test_kalshi_ticker_to_quote():
    message = {
        "type": "ticker", "sid": 2, "sending_ts_ms": 1791380000123,
        "msg": {
            "market_ticker": "KXMLBGAME-26OCT07TBNYY-NYY",
            "yes_bid_dollars": "0.6000", "yes_ask_dollars": "0.6100",
            "price_dollars": "0.6100",
            "yes_bid_size_fp": "1200", "yes_ask_size_fp": "800",
            "ts_ms": 1791380000100,
        },
        "recv_ts": RECV,
    }
    ((table, row),) = parse_kalshi(message)
    assert table == "quotes"
    assert row["venue"] == "kalshi"
    assert row["market_id"] == "KXMLBGAME-26OCT07TBNYY-NYY"
    assert row["best_bid"] == "0.6000" and row["best_ask"] == "0.6100"
    assert row["venue_ts"].isoformat() == "2026-10-07T13:33:20.100000+00:00"
    assert row["raw"] is message


def test_kalshi_acks_and_errors_skipped():
    for message in (
        {"type": "subscribed", "msg": {"channel": "ticker", "sid": 2}, "recv_ts": RECV},
        {"type": "error", "msg": {"code": 8, "msg": "Unknown channel"}, "recv_ts": RECV},
        {"type": "brand_new_mystery_type", "recv_ts": RECV},
    ):
        assert parse_kalshi(message) == []


def test_polymarket_book_to_book_and_quote():
    message = {
        "event_type": "book", "asset_id": "107183", "timestamp": "1791380000456",
        "bids": [{"price": "0.59", "size": "100"}, {"price": "0.60", "size": "50"}],
        "asks": [{"price": "0.62", "size": "40"}, {"price": "0.61", "size": "90"}],
        "hash": "abc", "recv_ts": RECV,
    }
    (bt, book), (qt, quote) = parse_polymarket(message)
    assert (bt, qt) == ("books", "quotes")
    assert book["bids"][0] == ["0.60", "50"]      # sorted best-first
    assert book["asks"][0] == ["0.61", "90"]
    assert quote["best_bid"] == "0.60" and quote["best_ask"] == "0.61"
    assert quote["venue_ts"].isoformat() == "2026-10-07T13:33:20.456000+00:00"


def test_polymarket_empty_book_sides():
    message = {
        "event_type": "book", "asset_id": "107183", "bids": [], "asks": [],
        "recv_ts": RECV,
    }
    (_, book), (_, quote) = parse_polymarket(message)
    assert book["bids"] == [] and quote["best_bid"] is None and quote["best_ask"] is None


def test_polymarket_price_change_both_tokens_and_sentinels():
    message = {
        "event_type": "price_change", "timestamp": "1791380000456",
        "price_changes": [
            {"asset_id": "107183", "best_bid": "0.60", "best_ask": "0.61",
             "price": "0.60", "size": "25", "side": "BUY"},
            {"asset_id": "490551", "best_bid": "0", "best_ask": "1",
             "price": "0.40", "size": "25", "side": "SELL"},
        ],
        "recv_ts": RECV,
    }
    rows = parse_polymarket(message)
    assert [t for t, _ in rows] == ["quotes", "quotes"]
    ours, complement = rows[0][1], rows[1][1]
    assert ours["best_bid"] == "0.60"
    # empty-side sentinels stored as NULL, raw keeps them
    assert complement["best_bid"] is None and complement["best_ask"] is None
    assert complement["raw"]["price_changes"][1]["best_bid"] == "0"


def test_polymarket_trade():
    message = {
        "event_type": "last_trade_price", "asset_id": "107183",
        "price": "0.999", "size": "14.91", "side": "SELL", "recv_ts": RECV,
    }
    ((table, row),) = parse_polymarket(message)
    assert table == "trades" and row["price"] == "0.999" and row["aggressor"] == "SELL"
