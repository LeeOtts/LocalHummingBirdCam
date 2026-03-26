"""Shared memory profiling utilities for tests and scripts.

Uses tracemalloc (stdlib) for per-allocation tracking and /proc/meminfo
for system-level RAM on Linux/Pi.  No extra dependencies required.
"""

import csv
import gc
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MemorySnapshot:
    """A single memory measurement."""
    label: str
    timestamp: float = field(default_factory=time.time)
    traced_current: int = 0   # bytes currently traced by tracemalloc
    traced_peak: int = 0      # peak bytes since last reset
    proc_available_mb: float | None = None  # from /proc/meminfo
    proc_used_mb: float | None = None

    def __str__(self):
        traced_kb = self.traced_current / 1024
        peak_kb = self.traced_peak / 1024
        sys_part = ""
        if self.proc_available_mb is not None:
            sys_part = f"  sys={self.proc_used_mb:.0f}/{self.proc_used_mb + self.proc_available_mb:.0f}MB"
        return f"[{self.label}] traced={traced_kb:.0f}KB  peak={peak_kb:.0f}KB{sys_part}"


def read_proc_meminfo() -> dict | None:
    """Read /proc/meminfo and return key values in KB.  Returns None on non-Linux."""
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
            return info
    except (FileNotFoundError, OSError):
        return None


def take_snapshot(label: str) -> MemorySnapshot:
    """Take a memory snapshot using tracemalloc + /proc/meminfo."""
    current, peak = tracemalloc.get_traced_memory()
    snap = MemorySnapshot(
        label=label,
        traced_current=current,
        traced_peak=peak,
    )
    meminfo = read_proc_meminfo()
    if meminfo:
        total_mb = meminfo.get("MemTotal", 0) / 1024
        available_mb = meminfo.get("MemAvailable", 0) / 1024
        snap.proc_available_mb = available_mb
        snap.proc_used_mb = total_mb - available_mb
    return snap


def log_snapshots_to_csv(snapshots: list[MemorySnapshot], path: Path):
    """Write snapshots as CSV for later analysis."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "label", "traced_current_kb", "traced_peak_kb",
            "proc_available_mb", "proc_used_mb",
        ])
        for s in snapshots:
            writer.writerow([
                f"{s.timestamp:.3f}",
                s.label,
                f"{s.traced_current / 1024:.1f}",
                f"{s.traced_peak / 1024:.1f}",
                f"{s.proc_available_mb:.1f}" if s.proc_available_mb is not None else "",
                f"{s.proc_used_mb:.1f}" if s.proc_used_mb is not None else "",
            ])


class TracmallocSession:
    """Context manager for tracemalloc-based memory profiling."""

    def __init__(self, nframes: int = 10):
        self.nframes = nframes
        self.snapshots: list[MemorySnapshot] = []

    def __enter__(self):
        gc.collect()
        tracemalloc.start(self.nframes)
        return self

    def __exit__(self, *exc):
        tracemalloc.stop()

    def snapshot(self, label: str) -> MemorySnapshot:
        """Take and store a snapshot."""
        snap = take_snapshot(label)
        self.snapshots.append(snap)
        return snap

    def reset_peak(self):
        """Reset the peak counter for delta measurements."""
        tracemalloc.reset_peak()

    def delta_kb(self, label_a: str, label_b: str) -> float:
        """Return traced_current difference (KB) between two labeled snapshots."""
        a = next(s for s in self.snapshots if s.label == label_a)
        b = next(s for s in self.snapshots if s.label == label_b)
        return (b.traced_current - a.traced_current) / 1024
