#!/usr/bin/env python3
"""Long-running memory profiler for the hummingbird camera system.

Instantiates real components with synthetic frames (no camera needed),
takes periodic memory snapshots, and writes them to CSV.

Usage:
    python scripts/memory_profile.py [--duration 3600] [--interval 30] [--output logs/memory_profile.csv]
"""

import argparse
import gc
import signal
import sys
import time
import tracemalloc
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

import config
from tests.memory_helpers import MemorySnapshot, log_snapshots_to_csv, take_snapshot


def _make_lores_frame():
    """Random 320x240 BGR frame for motion detection."""
    return np.random.randint(0, 255, (config.LORES_HEIGHT, config.LORES_WIDTH, 3), dtype=np.uint8)


def _make_hires_frame():
    """Random full-res BGR frame for buffer filling."""
    return np.random.randint(
        0, 255, (config.VIDEO_HEIGHT, config.VIDEO_WIDTH, 3), dtype=np.uint8
    )


def main():
    parser = argparse.ArgumentParser(description="Memory profiler for hummingbird cam")
    parser.add_argument("--duration", type=int, default=300, help="Run duration in seconds (default: 300)")
    parser.add_argument("--interval", type=int, default=10, help="Snapshot interval in seconds (default: 10)")
    parser.add_argument("--output", type=str, default=None, help="CSV output path")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else config.BASE_DIR / "logs" / "memory_profile.csv"

    snapshots: list[MemorySnapshot] = []
    running = True

    def handle_signal(signum, frame):
        nonlocal running
        running = False
        print("\nStopping...")

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(f"Memory profiler — {args.duration}s duration, {args.interval}s interval")
    print(f"Output: {output_path}")
    print()

    tracemalloc.start(10)

    # --- Phase 1: Component initialization ---
    snapshots.append(take_snapshot("baseline"))
    print(snapshots[-1])

    from camera.stream import FrameBuffer
    buffer_size = int(config.VIDEO_FPS * config.CLIP_PRE_SECONDS * 1.1)
    buf = FrameBuffer(maxlen=buffer_size)
    snapshots.append(take_snapshot("after_framebuffer_init"))
    print(snapshots[-1])

    # Fill the buffer
    print(f"Filling FrameBuffer with {buffer_size} frames...")
    for i in range(buffer_size):
        buf.add(_make_hires_frame())
    snapshots.append(take_snapshot("after_framebuffer_fill"))
    print(snapshots[-1])

    from detection.motion_color import MotionColorDetector
    detector = MotionColorDetector()
    snapshots.append(take_snapshot("after_detector_init"))
    print(snapshots[-1])

    # Try loading the real TFLite classifier (only works on Pi with model downloaded)
    classifier_loaded = False
    try:
        from detection.vision_verify import _load_model, verify_hummingbird
        _load_model()
        snapshots.append(take_snapshot("after_classifier_load"))
        print(snapshots[-1])
        classifier_loaded = True
    except Exception as e:
        print(f"Classifier not available ({e}) — skipping classifier profiling")
        snapshots.append(take_snapshot("classifier_skipped"))

    # --- Phase 2: Timed loop ---
    print(f"\nStarting {args.duration}s profiling loop (Ctrl+C to stop early)...")
    start_time = time.time()
    cycle = 0

    while running and (time.time() - start_time) < args.duration:
        cycle += 1
        cycle_start = time.time()

        # Simulate a detection cycle
        frame = _make_lores_frame()
        detector.detect(frame)

        if classifier_loaded and cycle % 5 == 0:
            hires = _make_hires_frame()
            try:
                verify_hummingbird(hires)
            except Exception:
                pass

        # Periodic GC (matches main.py pattern)
        if cycle % 10 == 0:
            gc.collect()

        # Take snapshot at each interval
        elapsed = time.time() - start_time
        if cycle == 1 or elapsed % args.interval < 1.0:
            snap = take_snapshot(f"cycle_{cycle}")
            snapshots.append(snap)

            # Print periodic status
            status = f"  [{elapsed:6.0f}s] cycle {cycle:4d}  traced={snap.traced_current / 1024:.0f}KB"
            if snap.proc_available_mb is not None:
                status += f"  avail={snap.proc_available_mb:.0f}MB"
            print(status)

        # Pace to roughly match real detection loop timing
        cycle_elapsed = time.time() - cycle_start
        sleep_time = max(0, 0.067 - cycle_elapsed)  # ~15 fps
        if sleep_time > 0:
            time.sleep(sleep_time)

    # --- Phase 3: Summary ---
    gc.collect()
    snapshots.append(take_snapshot("final"))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    # Memory timeline
    first = snapshots[0]
    last = snapshots[-1]
    growth_kb = (last.traced_current - first.traced_current) / 1024
    print(f"Total traced growth: {growth_kb:+.0f} KB")
    print(f"Peak traced:         {last.traced_peak / 1024:.0f} KB")

    if last.proc_available_mb is not None:
        avail_values = [s.proc_available_mb for s in snapshots if s.proc_available_mb is not None]
        print(f"System available:    min={min(avail_values):.0f}MB  max={max(avail_values):.0f}MB  "
              f"mean={sum(avail_values)/len(avail_values):.0f}MB")

    # Top allocations
    print("\nTop 10 allocations by file:")
    snap = tracemalloc.take_snapshot()
    stats = snap.statistics("filename")
    for i, stat in enumerate(stats[:10]):
        print(f"  {i+1}. {stat.size / 1024:8.0f} KB  {stat.traceback}")

    tracemalloc.stop()

    # Write CSV
    log_snapshots_to_csv(snapshots, output_path)
    print(f"\nSnapshots written to {output_path}")
    print(f"Total cycles: {cycle}, duration: {time.time() - start_time:.0f}s")


if __name__ == "__main__":
    main()
