"""Heartbeat: is every stream still recording? Exit 1 and say so if not.

    python -m tempo.monitor [--max-age-minutes 10] [--raw-dir data/raw]

Checks the newest recv_ts in each stream's latest JSONL file. A stream whose
last message is older than the threshold (or that has no file today) is
reported stale. Designed for cron:

    */10 * * * * cd /app && python -m tempo.monitor || curl -fsS "$ALERT_URL" -d "$(python -m tempo.monitor 2>&1)"

(Any webhook works for ALERT_URL — ntfy.sh topics and Discord webhooks are
free. The monitor itself only prints and sets the exit code; delivery is
deliberately someone else's job, so alerting can change without touching
this code.)

Why recv_ts from the files rather than asking the collector: the files are
the product. A collector that looks alive but writes nothing is exactly the
failure this must catch, so measure the product, not the process.

Caveat: quiet markets produce few messages. Keep at least one liquid, active
market in the watchlist per stream, or raise --max-age-minutes, so silence
means "feed is dead", not "slow afternoon".
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .collector import STREAMS


def last_recv_ts(raw_dir: Path, stream: str) -> datetime | None:
    """Newest recv_ts for a stream, from the tail of its newest file."""
    files = sorted(raw_dir.glob(f"{stream}_*.jsonl"))
    if not files:
        return None
    # Read the last non-empty line without loading the whole file.
    with files[-1].open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 65536))
        lines = f.read().splitlines()
    for line in reversed(lines):
        if line.strip():
            return datetime.fromisoformat(json.loads(line)["recv_ts"])
    return None


def check(raw_dir: Path, max_age: timedelta, now: datetime | None = None) -> list[str]:
    """One problem string per stale stream; empty list = all healthy."""
    now = now or datetime.now(timezone.utc)
    problems = []
    for stream in STREAMS:
        last = last_recv_ts(raw_dir, stream)
        if last is None:
            problems.append(f"{stream}: no data files in {raw_dir}")
        elif now - last > max_age:
            age_min = (now - last).total_seconds() / 60
            problems.append(f"{stream}: last message {age_min:.0f} min ago (recv_ts {last.isoformat()})")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-age-minutes", type=float, default=10)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    problems = check(args.raw_dir, timedelta(minutes=args.max_age_minutes))
    if problems:
        print("STALE FEEDS:\n" + "\n".join(f"  {p}" for p in problems))
        sys.exit(1)
    print("all feeds healthy")


if __name__ == "__main__":
    main()
