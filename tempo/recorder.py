"""Raw JSONL recorder — the collector's one unbreakable promise.

Every venue message is written exactly as received, plus recv_ts (our UTC
receipt time, milliseconds). Files roll by UTC date per stream name:

    data/raw/<stream>_<YYYY-MM-DD>.jsonl

Unlike the Phase 0 scripts, the Recorder keeps the file handle open between
writes (a few thousand messages per minute on game nights makes per-write
open/close measurable) and reopens when the UTC date rolls over.

Postgres loading is a separate, later concern: raw JSONL is the source of
truth, and the database can always be rebuilt from it. Disk-first means a
database outage never loses data.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import IO


class Recorder:
    def __init__(self, stream: str, raw_dir: Path = Path("data/raw")) -> None:
        self.stream = stream
        self.raw_dir = raw_dir
        self._file: IO[str] | None = None
        self._file_date: str | None = None

    def _file_for(self, recv: datetime) -> IO[str]:
        date = f"{recv:%Y-%m-%d}"
        if self._file is None or date != self._file_date:
            if self._file is not None:
                self._file.close()
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            self._file = (self.raw_dir / f"{self.stream}_{date}.jsonl").open("a")
            self._file_date = date
        return self._file

    def write(self, message: dict, recv: datetime | None = None) -> datetime:
        """Append one raw message; returns the recv_ts used."""
        recv = recv or datetime.now(timezone.utc)
        if "recv_ts" in message:
            raise ValueError(f"venue message already has a recv_ts field: {message}")
        f = self._file_for(recv)
        f.write(json.dumps({**message, "recv_ts": recv.isoformat(timespec="milliseconds")}) + "\n")
        f.flush()  # a crash loses at most the OS buffer, not our queue
        return recv

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
