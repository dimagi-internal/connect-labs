"""ffmpeg wrapper for concatenating walkthrough webms/mp4s.

The PAR walkthrough produces two recordings (manager-flow prepend +
drill-through) that need to be stitched into one MP4 for sharing. The
filter graph is finicky — wrong concat invocation either silently drops
audio or stretches frames. This wrapper bakes in the working version
the original recorders prove works.

Usage::

    python -m scripts.walkthroughs._lib.concat \
        manager_flow.mp4 drill_through.mp4 --out program-admin-report.mp4

Inputs may be ``.webm`` (Playwright's native output) or ``.mp4``; ffmpeg
handles the demux either way.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def concat(inputs: list[Path], out: Path, *, crf: int = 23) -> None:
    """Concatenate video files into a single MP4 using libx264 (CRF 23).

    Re-encodes rather than stream-copies — Playwright's webm uses a
    different codec than the typical MP4 output, and ``-c copy`` fails
    on codec mismatch. CRF 23 is the working trade-off in the original
    recorder: high enough to keep cursor strokes crisp, low enough that
    a 2-minute video stays under ~10 MB.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit(
            "ERROR: ffmpeg not found on PATH. Install it (`brew install ffmpeg`) " "before running concat."
        )
    if not inputs:
        raise SystemExit("ERROR: no input files given.")
    missing = [str(p) for p in inputs if not p.exists()]
    if missing:
        raise SystemExit(f"ERROR: input files not found: {missing}")

    filter_complex = "".join(f"[{i}:v]" for i in range(len(inputs))) + f"concat=n={len(inputs)}:v=1:a=0[v]"
    cmd: list[str] = ["ffmpeg", "-y"]
    for p in inputs:
        cmd.extend(["-i", str(p)])
    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
    )
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    print(f"Wrote {out} ({out.stat().st_size / 1024 / 1024:.1f} MB)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("inputs", nargs="+", type=Path, help="Input video files in order.")
    p.add_argument("--out", required=True, type=Path, help="Output MP4 path.")
    p.add_argument("--crf", type=int, default=23, help="x264 CRF (default 23).")
    args = p.parse_args(argv)
    concat(args.inputs, args.out, crf=args.crf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
