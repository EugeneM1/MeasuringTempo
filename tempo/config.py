"""Collector configuration, loaded from watchlist.json at the repo root.

Phase 0 lesson (LEARNING.md): generic "open markets" listings don't scale —
one strike ladder floods every page. The collector therefore subscribes by
explicit identifier. watchlist.json shape:

    {
      "kalshi_tickers":    ["KXMLBGAME-26OCT07TBNYY-NYY", ...],
      "polymarket_tokens": ["10718358777413591571...", ...]
    }

Series-based auto-discovery (watch everything in KXMLBGAME as games appear)
is a planned Phase 1 extension; explicit lists keep the first deployment
simple and auditable.

Secrets stay in .env (KALSHI_KEY_ID, KALSHI_KEY_PATH), never in watchlist.json.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    kalshi_tickers: list[str]
    polymarket_tokens: list[str]
    kalshi_key_id: str
    kalshi_key_path: str
    raw_dir: Path


def load(watchlist_path: str | Path = "watchlist.json") -> Config:
    load_dotenv()
    watchlist = json.loads(Path(watchlist_path).read_text())
    # Keys starting with "_" are comments, ignored.
    unknown = {k for k in watchlist if not k.startswith("_")} - {"kalshi_tickers", "polymarket_tokens"}
    if unknown:
        raise ValueError(f"unknown keys in {watchlist_path}: {sorted(unknown)}")
    return Config(
        kalshi_tickers=watchlist.get("kalshi_tickers", []),
        polymarket_tokens=watchlist.get("polymarket_tokens", []),
        kalshi_key_id=os.environ["KALSHI_KEY_ID"],
        kalshi_key_path=os.environ["KALSHI_KEY_PATH"],
        raw_dir=Path(os.environ.get("TEMPO_RAW_DIR", "data/raw")),
    )
