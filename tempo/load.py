"""Batch loader: data/raw/*.jsonl -> Postgres, idempotently.

    python -m tempo.load "postgresql://tempo:...@localhost/tempo" [data/raw]

Design:
- The raw files stay the source of truth; this is a rebuildable view. The
  loader tracks progress per file in loaded_files (row count at last load),
  so re-running only processes new lines — safe to cron hourly, safe to
  re-run after a crash, and a full rebuild is TRUNCATE + delete the
  bookmarks + run again.
- Parsing is tempo.parse; this module only moves rows. Inserts are batched
  with executemany per table per file.
- Skipped message types are counted and logged, never silently dropped
  (they remain in the raw files).
"""

import json
import logging
import sys
from collections import Counter
from pathlib import Path

from .parse import PARSERS

log = logging.getLogger("tempo.load")

COLUMNS = {
    "quotes": ["venue", "market_id", "recv_ts", "venue_ts", "best_bid", "best_ask", "bid_size", "ask_size", "raw"],
    "trades": ["venue", "market_id", "recv_ts", "venue_ts", "price", "size", "aggressor", "raw"],
    "books":  ["venue", "market_id", "recv_ts", "venue_ts", "bids", "asks", "raw"],
}
JSONB_COLUMNS = {"raw", "bids", "asks"}

BOOKMARKS_DDL = """
CREATE TABLE IF NOT EXISTS loaded_files (
    file_name    text PRIMARY KEY,
    lines_loaded bigint NOT NULL,
    loaded_at    timestamptz NOT NULL DEFAULT now()
)
"""


def rows_from_file(path: Path, skip_lines: int = 0) -> tuple[dict[str, list[list]], Counter, int]:
    """Parse one JSONL file (after skip_lines) into per-table value lists.

    Returns (rows_by_table, skip_counter, total_lines_seen). Pure function —
    all the logic, none of the database — so tests cover it directly.
    """
    # Filename is <stream>_<YYYY-MM-DD>.jsonl; the date has no underscores,
    # so everything before the LAST underscore is the stream name.
    stream = path.stem[: path.stem.rfind("_")]
    parser = PARSERS[stream]
    by_table: dict[str, list[list]] = {t: [] for t in COLUMNS}
    skipped: Counter = Counter()
    n = 0
    with path.open() as f:
        for n, line in enumerate(f, start=1):
            if n <= skip_lines:
                continue
            message = json.loads(line)
            parsed = parser(message)
            if not parsed:
                skipped[message.get("type") or message.get("event_type") or "?"] += 1
            for table, row in parsed:
                by_table[table].append(
                    [json.dumps(row[c]) if c in JSONB_COLUMNS else row[c] for c in COLUMNS[table]]
                )
    return by_table, skipped, n


def load_dir(conn, raw_dir: Path) -> None:
    """Load every new line of every raw file into Postgres."""
    with conn.cursor() as cur:
        cur.execute(BOOKMARKS_DDL)
        cur.execute("SELECT file_name, lines_loaded FROM loaded_files")
        bookmarks = dict(cur.fetchall())
    for path in sorted(raw_dir.glob("*.jsonl")):
        done = bookmarks.get(path.name, 0)
        by_table, skipped, total = rows_from_file(path, skip_lines=done)
        if total == done:
            continue
        with conn.cursor() as cur:
            for table, rows in by_table.items():
                if not rows:
                    continue
                cols = COLUMNS[table]
                placeholders = ", ".join(["%s"] * len(cols))
                cur.executemany(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})", rows
                )
            cur.execute(
                "INSERT INTO loaded_files (file_name, lines_loaded) VALUES (%s, %s) "
                "ON CONFLICT (file_name) DO UPDATE SET lines_loaded = EXCLUDED.lines_loaded, loaded_at = now()",
                (path.name, total),
            )
        conn.commit()
        log.info(
            "%s: +%d lines -> %s; skipped %s",
            path.name, total - done,
            {t: len(r) for t, r in by_table.items() if r}, dict(skipped) or "none",
        )


def main() -> None:
    import psycopg

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    dsn = sys.argv[1]
    raw_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/raw")
    with psycopg.connect(dsn) as conn:
        load_dir(conn, raw_dir)


if __name__ == "__main__":
    main()
