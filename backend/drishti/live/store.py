"""Tick files, 1m-bar Parquet, retention and the disk guard (live recorder).

Ticks: /windows/drishti_data/ticks/YYYY-MM-DD/ticks_HHMM.zst, one file per 15 minutes. Each
frame is the raw V3 protobuf message exactly as received, prefixed by
  8 bytes  receive time, ns since epoch (big endian)
  4 bytes  length (big endian)
so a later reader can replay the feed byte for byte.
1m bars: /windows/drishti_data/bars_1m/YYYY-MM-DD/bars_HHMM.parquet (closed minutes only).
Retention: tick day-directories older than RETENTION_DAYS are deleted at recorder start; only
directories whose name is a date are considered. Disk guard: below GUARD_GB free on the tick
filesystem, tick writing stops (the live view and 1m bars go on) and an alert is raised.
"""

from __future__ import annotations

import datetime as _dt
import re
import shutil
import struct
import time
from pathlib import Path

import zstandard

RETENTION_DAYS = 30            # user 2026-10-29 (60 later, after the disk cleanup 3.1)
GUARD_GB = 10.0
ROTATE_MIN = 15
DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 1e9


class TickWriter:
    def __init__(self, root: Path, guard_gb: float = GUARD_GB):
        self.root, self.guard_gb = root, guard_gb
        self._f = self._z = None
        self._slot: str | None = None
        self.frames = self.raw_bytes = 0
        self.stopped_by_guard = False
        self.files: list[Path] = []

    def _open(self, t: _dt.datetime) -> None:
        slot = f"{t:%Y-%m-%d}/ticks_{t:%H}{t.minute // ROTATE_MIN * ROTATE_MIN:02d}.zst"
        if slot == self._slot:
            return
        self.close()
        p = self.root / slot
        p.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(p, "ab")
        self._z = zstandard.ZstdCompressor(level=6).stream_writer(self._f)
        self._slot = slot
        self.files.append(p)

    def append(self, data: bytes, recv: _dt.datetime) -> bool:
        if self.stopped_by_guard:
            return False
        self._open(recv)
        self._z.write(struct.pack(">qI", time.time_ns(), len(data)) + data)
        self.frames += 1
        self.raw_bytes += len(data)
        return True

    def check_guard(self) -> bool:
        """True when tick writing must stop (and closes the file)."""
        if not self.stopped_by_guard and free_gb(self.root) < self.guard_gb:
            self.stopped_by_guard = True
            self.close()
        return self.stopped_by_guard

    def close(self) -> None:
        if self._z is not None:
            self._z.flush(zstandard.FLUSH_FRAME)
            self._z.close()
        self._f = self._z = None
        self._slot = None


def read_frames(path: Path):
    """Yield (recv_ns, raw protobuf bytes) from one tick file (tests, replay)."""
    with open(path, "rb") as f:
        data = zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True).read()
    i = 0
    while i + 12 <= len(data):
        ns, n = struct.unpack(">qI", data[i:i + 12])
        yield ns, data[i + 12:i + 12 + n]
        i += 12 + n


def prune(root: Path, today: _dt.date, keep_days: int = RETENTION_DAYS) -> list[str]:
    """Delete tick day-directories older than keep_days (names must be dates)."""
    gone = []
    if not root.exists():
        return gone
    for d in sorted(root.iterdir()):
        if d.is_dir() and DAY_DIR.match(d.name) and \
                (today - _dt.date.fromisoformat(d.name)).days > keep_days:
            shutil.rmtree(d)
            gone.append(d.name)
    return gone


def write_bars(root: Path, day: _dt.date, rows: list[dict], stamp: _dt.datetime) -> Path | None:
    if not rows:
        return None
    import pyarrow as pa
    import pyarrow.parquet as pq
    p = root / str(day) / f"bars_{stamp:%H%M%S}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), p, compression="zstd")
    return p


def day_gb(root: Path, day: _dt.date) -> float:
    d = root / str(day)
    return sum(f.stat().st_size for f in d.glob("*.zst")) / 1e9 if d.exists() else 0.0
