"""Compress static "loading screen" runs in a recorded walkthrough video.

Why this exists
---------------
Playwright records a continuous stream at ~30fps. Whenever the page is
waiting on a slow labs request (e.g. bulk-assessment images streaming
from GDrive, the labs auth-context middleware checking the OAuth token),
the frames are visually identical — a spinner over a "Loading…" header
— but every one of those identical frames is still in the file. A
14-minute drill-through recording on a slow labs day is mostly those
runs.

This module detects sequences of near-identical frames and **collapses
each into a fixed short window** (default 1.0s), so the final video keeps
"the page was loading something" as a visual beat but doesn't waste
viewer attention on it.

How it detects "loading"
------------------------
We use a frame-diff heuristic (ffmpeg's ``mpdecimate`` filter style) plus
a brightness floor. Frames are read at 5fps, hashed (perceptual hash via
average-brightness vector across a 16x16 grid), and consecutive frames
whose hash distance is under ``IDENTITY_THRESHOLD`` are treated as the
same scene. Runs longer than ``MAX_STATIC_SECONDS`` are clipped to that
duration. We deliberately don't try to read the "Loading…" text — the
content-agnostic approach catches auth gates, network spinners, and the
PAR snapshot polling loop equally well, and it works without OCR.

For pages where the spinner ANIMATES (rotating SVG), animation movement
is local; the rest of the frame is static. We compute the diff over the
full frame, so the rotating spinner alone barely moves the global hash.
That keeps spinner-only frames classified as "static" and lets us
compress them. If the spinner area gets too much weight in the diff,
tune ``IDENTITY_THRESHOLD`` upward.

Usage
-----
::

    python -m scripts.walkthroughs._lib.compress_loading INPUT.mp4 \\
        --out OUTPUT.mp4 --max-static 1.0

Idempotent: a file already compressed has no static runs above the
threshold; re-running is a no-op (re-encoded but no segments dropped).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Frame hash grid is 16x16 luminance averages. Distance is sum of abs
# differences across all cells; max possible is ~16*16*255 ≈ 65k. Frames
# from the same "loading" state usually score < 800 even with a rotating
# spinner; scene transitions score in the thousands.
IDENTITY_THRESHOLD = 1200
SAMPLE_FPS = 5


def _hash_frame(image_bytes: bytes) -> bytes:
    """Cheap perceptual hash: downsample to 16x16 luminance, return raw bytes.

    Distance between two hashes = sum(|a - b|) for matched cells. Cheap
    enough to run on every sampled frame without slowing down the encode
    pipeline.
    """
    # Pillow is the easiest no-fuss dependency for this. The labs venv
    # already has it via Django imageops. If you're running this script
    # outside the labs venv: ``pip install Pillow``.
    from io import BytesIO

    from PIL import Image

    img = Image.open(BytesIO(image_bytes)).convert("L").resize((16, 16))
    return img.tobytes()


def _hash_distance(a: bytes, b: bytes) -> int:
    return sum(abs(a[i] - b[i]) for i in range(len(a)))


def _sample_frames(input_path: Path, fps: int) -> list[tuple[float, bytes]]:
    """Walk the input video at ``fps`` samples/sec and return per-sample
    (timestamp_seconds, perceptual_hash) tuples. Uses ffmpeg + an internal
    PNG pipe so we don't have to drop temp files for every frame.
    """
    cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vf",
        f"fps={fps}",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    assert proc.stdout is not None
    samples: list[tuple[float, bytes]] = []
    # Stream PNGs out of the pipe — each starts with the PNG signature
    # b"\x89PNG\r\n\x1a\n". Buffer until we see two of them, emit the first,
    # repeat. Simple framing; fine at our throughput.
    PNG_SIG = b"\x89PNG\r\n\x1a\n"
    buf = b""
    sample_idx = 0
    while True:
        chunk = proc.stdout.read(65536)
        if not chunk:
            break
        buf += chunk
        while True:
            first_sig = buf.find(PNG_SIG)
            if first_sig < 0:
                break
            next_sig = buf.find(PNG_SIG, first_sig + 8)
            if next_sig < 0:
                break
            png = buf[first_sig:next_sig]
            buf = buf[next_sig:]
            ts = sample_idx / fps
            samples.append((ts, _hash_frame(png)))
            sample_idx += 1
    # final chunk
    if buf.startswith(PNG_SIG):
        ts = sample_idx / fps
        samples.append((ts, _hash_frame(buf)))
    proc.wait()
    return samples


def find_keep_ranges(
    samples: list[tuple[float, bytes]],
    *,
    identity_threshold: int = IDENTITY_THRESHOLD,
    max_static_seconds: float = 1.0,
) -> list[tuple[float, float]]:
    """Return ``[(start, end), …]`` seconds-ranges to keep.

    A "static run" is a maximal sequence of consecutive samples whose
    pairwise distance stays below ``identity_threshold``. Runs longer
    than ``max_static_seconds`` get truncated to that duration; shorter
    runs are kept as-is. Non-static segments (anything with motion)
    pass through untouched.
    """
    if not samples:
        return []

    keep: list[tuple[float, float]] = []
    run_start = samples[0][0]
    run_hash = samples[0][1]
    last_motion_ts = samples[0][0]

    for i in range(1, len(samples)):
        ts, h = samples[i]
        if _hash_distance(h, run_hash) < identity_threshold:
            # still static
            continue
        # motion: close out the static run
        static_end = min(ts, last_motion_ts + max_static_seconds)
        # if the run already exceeded the cap, clip; otherwise keep all of it.
        keep.append((run_start, max(static_end, last_motion_ts + 0.001)))
        run_start = ts
        run_hash = h
        last_motion_ts = ts

    # final segment
    final_end = samples[-1][0] + 1.0 / SAMPLE_FPS
    static_end = min(final_end, last_motion_ts + max_static_seconds)
    keep.append((run_start, max(static_end, last_motion_ts + 0.001)))

    # Merge overlapping/adjacent ranges that ended up touching (within
    # one sample tick).
    merged: list[tuple[float, float]] = []
    for a, b in keep:
        if merged and a - merged[-1][1] < 1.0 / SAMPLE_FPS:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def compress(
    input_path: Path,
    output_path: Path,
    *,
    max_static_seconds: float = 1.0,
    identity_threshold: int = IDENTITY_THRESHOLD,
    sample_fps: int = SAMPLE_FPS,
    crf: int = 23,
) -> None:
    """Compress static runs in ``input_path``, write to ``output_path``.

    The naming reflects intent — the resulting file is shorter than the
    input, with every "page is loading" pause clipped to about
    ``max_static_seconds``. Spinner motion does NOT prevent compression
    (the global frame hash treats a rotating spinner as static-ish).
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ERROR: ffmpeg not on PATH. brew install ffmpeg")

    print(f"Sampling {input_path.name} at {sample_fps}fps…")
    samples = _sample_frames(input_path, sample_fps)
    if not samples:
        raise SystemExit("ERROR: no frames sampled. is the input a valid video?")

    duration_in = samples[-1][0]
    keep = find_keep_ranges(
        samples,
        identity_threshold=identity_threshold,
        max_static_seconds=max_static_seconds,
    )
    duration_out = sum(end - start for start, end in keep)
    print(f"  in:  {duration_in:.1f}s  →  out: {duration_out:.1f}s  ({len(keep)} ranges)")

    # Cut + concatenate via ffmpeg's concat-demuxer. Write a manifest of
    # temp clips, encode each, then concat-copy.
    with tempfile.TemporaryDirectory(prefix="compress_loading_") as td:
        td_path = Path(td)
        clips: list[Path] = []
        for i, (start, end) in enumerate(keep):
            clip = td_path / f"clip_{i:04d}.mp4"
            cmd = [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                f"{start:.3f}",
                "-to",
                f"{end:.3f}",
                "-i",
                str(input_path),
                "-c:v",
                "libx264",
                "-crf",
                str(crf),
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                str(clip),
            ]
            subprocess.run(cmd, check=True)
            clips.append(clip)

        manifest = td_path / "manifest.txt"
        manifest.write_text("\n".join(f"file '{c.as_posix()}'" for c in clips) + "\n")
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-c",
            "copy",
            str(output_path),
        ]
        subprocess.run(cmd, check=True)
    print(f"  wrote {output_path}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path, help="Input video.")
    p.add_argument("--out", required=True, type=Path, help="Output MP4 path.")
    p.add_argument(
        "--max-static",
        type=float,
        default=1.0,
        help=("Maximum seconds to keep of any static run. Default 1.0s — long enough "
              "to register 'we are loading' as a visual beat, short enough to feel snappy."),
    )
    p.add_argument(
        "--identity-threshold",
        type=int,
        default=IDENTITY_THRESHOLD,
        help=("Hash-distance below which two frames are considered the same scene. "
              "Increase for noisier video (animated spinners), decrease if real "
              "scene changes are being collapsed."),
    )
    p.add_argument(
        "--sample-fps",
        type=int,
        default=SAMPLE_FPS,
        help="Frames-per-second to sample for hash analysis.",
    )
    p.add_argument("--crf", type=int, default=23, help="x264 CRF quality knob.")
    args = p.parse_args(argv)

    compress(
        args.input,
        args.out,
        max_static_seconds=args.max_static,
        identity_threshold=args.identity_threshold,
        sample_fps=args.sample_fps,
        crf=args.crf,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
