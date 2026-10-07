"""Recorder: raw-plus-recv_ts on disk, date rollover, no double-stamping."""

import json
from datetime import datetime, timezone

import pytest

from tempo.recorder import Recorder


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_writes_raw_plus_recv_ts(tmp_path):
    rec = Recorder("teststream", raw_dir=tmp_path)
    recv = datetime(2026, 10, 7, 20, 0, 0, 123000, tzinfo=timezone.utc)
    rec.write({"type": "ticker", "msg": {"price": "0.61"}}, recv=recv)
    rec.close()
    (line,) = read_lines(tmp_path / "teststream_2026-10-07.jsonl")
    assert line["type"] == "ticker" and line["msg"] == {"price": "0.61"}
    assert line["recv_ts"] == "2026-10-07T20:00:00.123+00:00"


def test_date_rollover_opens_new_file(tmp_path):
    rec = Recorder("teststream", raw_dir=tmp_path)
    rec.write({"n": 1}, recv=datetime(2026, 10, 7, 23, 59, 59, tzinfo=timezone.utc))
    rec.write({"n": 2}, recv=datetime(2026, 10, 8, 0, 0, 1, tzinfo=timezone.utc))
    rec.close()
    assert [l["n"] for l in read_lines(tmp_path / "teststream_2026-10-07.jsonl")] == [1]
    assert [l["n"] for l in read_lines(tmp_path / "teststream_2026-10-08.jsonl")] == [2]


def test_appends_across_recorder_restarts(tmp_path):
    recv = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    for n in (1, 2):
        rec = Recorder("teststream", raw_dir=tmp_path)
        rec.write({"n": n}, recv=recv)
        rec.close()
    assert [l["n"] for l in read_lines(tmp_path / "teststream_2026-10-07.jsonl")] == [1, 2]


def test_rejects_message_that_already_has_recv_ts(tmp_path):
    rec = Recorder("teststream", raw_dir=tmp_path)
    with pytest.raises(ValueError):
        rec.write({"recv_ts": "imposter"})
