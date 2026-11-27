"""Snapshot: venue JSON -> uniform CSV rows, ranked by 24h volume. No network."""

import csv

import pytest

from tempo import snapshot

TS = "2026-11-26T20:17:00+00:00"

# Trimmed from real responses (Nov 2026). Note the venue quirks the parser
# must survive: Gamma's JSON-encoded-string lists, Kalshi's dollar strings.
GAMMA_MARKET = {
    "id": "6248123",
    "question": "Patriots vs. Lions",
    "conditionId": "0xecbfaf9f",
    "slug": "nfl-ne-det-2026-11-15",
    "endDate": "2026-11-15T14:30:00Z",
    "outcomes": '["Patriots", "Lions"]',
    "outcomePrices": '["0.015", "0.985"]',
    "volume24hr": 2278988.508725,
    "volumeNum": 2361125.891395,
    "bestBid": 0.014,
    "bestAsk": 0.016,
    "lastTradePrice": 0.015,
}

KALSHI_MARKET = {
    "ticker": "KXELONMARS-99",
    "event_ticker": "KXELONMARS-99",
    "title": "Will Elon Musk visit Mars before Aug 1, 2099?",
    "yes_sub_title": "Before 2099",
    "status": "active",
    "yes_bid_dollars": "0.1000",
    "yes_ask_dollars": "0.1300",
    "last_price_dollars": "0.1000",
    "volume_fp": "127842.05",
    "volume_24h_fp": "22.66",
    "close_time": "2099-08-01T04:59:00Z",
}


def test_polymarket_row_parses_string_encoded_outcomes():
    row = snapshot.polymarket_row(GAMMA_MARKET, TS)
    assert row["venue"] == "polymarket"
    assert row["market_id"] == "0xecbfaf9f"
    assert row["yes_outcome"] == "Patriots"
    assert (row["yes_bid"], row["yes_ask"], row["last_price"]) == (0.014, 0.016, 0.015)
    assert row["volume_24h"] == pytest.approx(2278988.508725)


def test_kalshi_row_converts_dollar_strings():
    row = snapshot.kalshi_row(KALSHI_MARKET, TS)
    assert row["venue"] == "kalshi"
    assert row["market_id"] == "KXELONMARS-99"
    assert (row["yes_bid"], row["yes_ask"], row["last_price"]) == (0.10, 0.13, 0.10)
    assert row["volume_24h"] == pytest.approx(22.66)
    assert row["volume_total"] == pytest.approx(127842.05)


def test_rows_have_exactly_the_csv_columns():
    for row in (snapshot.polymarket_row(GAMMA_MARKET, TS), snapshot.kalshi_row(KALSHI_MARKET, TS)):
        assert list(row) == snapshot.COLUMNS


def test_missing_prices_become_none_not_a_crash():
    sparse = {"ticker": "KXTHIN-1", "yes_bid_dollars": "", "volume_24h_fp": None}
    row = snapshot.kalshi_row(sparse, TS)
    assert row["yes_bid"] is None and row["yes_ask"] is None and row["volume_24h"] is None


def test_top_by_volume_ranks_and_truncates():
    rows = [{"volume_24h": v} for v in (5.0, None, 50.0, 0.5)]
    assert [r["volume_24h"] for r in snapshot.top_by_volume(rows, 2)] == [50.0, 5.0]


def test_fetch_kalshi_follows_cursor_and_skips_inactive(monkeypatch):
    closed = {**KALSHI_MARKET, "ticker": "KXCLOSED-1", "status": "closed"}
    busy = {**KALSHI_MARKET, "ticker": "KXBUSY-1", "volume_24h_fp": "9000.00"}
    pages = {
        None: {"events": [{"markets": [KALSHI_MARKET, closed]}], "cursor": "page2"},
        "page2": {"events": [{"markets": [busy]}, {"markets": None}], "cursor": ""},
    }
    monkeypatch.setattr(snapshot, "get_json", lambda url, params: pages[params.get("cursor")])
    rows = snapshot.fetch_kalshi(TS)
    assert [r["market_id"] for r in rows] == ["KXBUSY-1", "KXELONMARS-99"]


def test_fetch_polymarket_pages_until_empty(monkeypatch):
    def fake(url, params):
        return [GAMMA_MARKET] if params["offset"] == 0 else []

    monkeypatch.setattr(snapshot, "get_json", fake)
    assert len(snapshot.fetch_polymarket(TS)) == 1


def test_main_writes_dated_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(snapshot, "fetch_kalshi", lambda ts: [snapshot.kalshi_row(KALSHI_MARKET, ts)])
    monkeypatch.setattr(snapshot, "fetch_polymarket", lambda ts: [snapshot.polymarket_row(GAMMA_MARKET, ts)])
    snapshot.main(str(tmp_path))
    (path,) = tmp_path.glob("*/*.csv")
    rows = list(csv.DictReader(path.open()))
    assert [r["venue"] for r in rows] == ["kalshi", "polymarket"]
    assert rows[1]["title"] == "Patriots vs. Lions"


def test_main_refuses_to_write_when_a_venue_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(snapshot, "fetch_kalshi", lambda ts: [])
    monkeypatch.setattr(snapshot, "fetch_polymarket", lambda ts: [snapshot.polymarket_row(GAMMA_MARKET, ts)])
    with pytest.raises(SystemExit):
        snapshot.main(str(tmp_path))
    assert not list(tmp_path.rglob("*.csv"))
