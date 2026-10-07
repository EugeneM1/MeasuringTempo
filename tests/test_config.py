"""Config loading: watchlist + env secrets, unknown keys rejected."""

import json

import pytest

from tempo import config as config_mod


def test_loads_watchlist_and_env(tmp_path, monkeypatch):
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"kalshi_tickers": ["T1"], "polymarket_tokens": ["123"]}))
    monkeypatch.setenv("KALSHI_KEY_ID", "kid")
    monkeypatch.setenv("KALSHI_KEY_PATH", "/keys/k.pem")
    cfg = config_mod.load(wl)
    assert cfg.kalshi_tickers == ["T1"]
    assert cfg.polymarket_tokens == ["123"]
    assert cfg.kalshi_key_id == "kid"


def test_unknown_watchlist_keys_rejected(tmp_path, monkeypatch):
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"kalshi_tickers": [], "typo_key": []}))
    monkeypatch.setenv("KALSHI_KEY_ID", "kid")
    monkeypatch.setenv("KALSHI_KEY_PATH", "/keys/k.pem")
    with pytest.raises(ValueError, match="typo_key"):
        config_mod.load(wl)
