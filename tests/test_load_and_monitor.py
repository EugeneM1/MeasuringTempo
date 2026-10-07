"""Loader row generation (incremental bookmarks) and heartbeat staleness."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tempo.load import rows_from_file
from tempo.monitor import check, last_recv_ts

RECV = "2026-10-08T00:30:00.000+00:00"
TICKER = {
    "type": "ticker",
    "msg": {"market_ticker": "T1", "yes_bid_dollars": "0.60", "yes_ask_dollars": "0.61"},
    "recv_ts": RECV,
}
ACK = {"type": "subscribed", "msg": {"channel": "ticker", "sid": 1}, "recv_ts": RECV}


def write_jsonl(path: Path, messages) -> None:
    path.write_text("".join(json.dumps(m) + "\n" for m in messages))


def test_rows_from_file_parses_and_counts_skips(tmp_path):
    path = tmp_path / "kalshi_ws_2026-10-08.jsonl"
    write_jsonl(path, [ACK, TICKER, TICKER])
    by_table, skipped, total = rows_from_file(path)
    assert total == 3
    assert len(by_table["quotes"]) == 2 and not by_table["trades"]
    assert skipped == {"subscribed": 1}
    # raw column is JSON text ready for a jsonb insert
    assert json.loads(by_table["quotes"][0][-1])["type"] == "ticker"


def test_rows_from_file_incremental_skip(tmp_path):
    path = tmp_path / "kalshi_ws_2026-10-08.jsonl"
    write_jsonl(path, [TICKER, TICKER, TICKER])
    by_table, _, total = rows_from_file(path, skip_lines=2)
    assert total == 3 and len(by_table["quotes"]) == 1


def test_stream_name_with_underscores_resolves(tmp_path):
    path = tmp_path / "polymarket_ws_2026-10-08.jsonl"
    write_jsonl(path, [{"event_type": "last_trade_price", "asset_id": "1",
                        "price": "0.5", "recv_ts": RECV}])
    by_table, _, _ = rows_from_file(path)
    assert len(by_table["trades"]) == 1


def test_monitor_flags_missing_and_stale_streams(tmp_path):
    now = datetime(2026, 10, 8, 1, 0, tzinfo=timezone.utc)
    # kalshi: fresh file; polymarket: no file at all
    fresh = {**TICKER, "recv_ts": (now - timedelta(minutes=2)).isoformat(timespec="milliseconds")}
    write_jsonl(tmp_path / "kalshi_ws_2026-10-08.jsonl", [fresh])
    problems = check(tmp_path, max_age=timedelta(minutes=10), now=now)
    assert len(problems) == 1 and problems[0].startswith("polymarket_ws: no data files")

    # make polymarket exist but stale
    stale = {"event_type": "book", "asset_id": "1", "bids": [], "asks": [],
             "recv_ts": (now - timedelta(minutes=45)).isoformat(timespec="milliseconds")}
    write_jsonl(tmp_path / "polymarket_ws_2026-10-08.jsonl", [stale])
    problems = check(tmp_path, max_age=timedelta(minutes=10), now=now)
    assert len(problems) == 1 and "45 min ago" in problems[0]

    # both fresh -> healthy
    write_jsonl(tmp_path / "polymarket_ws_2026-10-08.jsonl", [{**stale, "recv_ts": fresh["recv_ts"]}])
    assert check(tmp_path, max_age=timedelta(minutes=10), now=now) == []


def test_last_recv_ts_reads_newest_file_tail(tmp_path):
    old = tmp_path / "kalshi_ws_2026-10-07.jsonl"
    new = tmp_path / "kalshi_ws_2026-10-08.jsonl"
    write_jsonl(old, [{**TICKER, "recv_ts": "2026-10-07T01:00:00.000+00:00"}])
    write_jsonl(new, [{**TICKER, "recv_ts": "2026-10-08T01:00:00.000+00:00"},
                      {**TICKER, "recv_ts": "2026-10-08T02:00:00.000+00:00"}])
    assert last_recv_ts(tmp_path, "kalshi_ws") == datetime(2026, 10, 8, 2, 0, tzinfo=timezone.utc)
