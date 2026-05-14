#!/usr/bin/env python3
"""Smoke-read a plate through forge-align's production extractor path.

This calls the SAME helpers cli_solve uses (forge_cv.extractor.read_sequence_frame
for image sequences, extract_container_frame for MOV/MXF/etc.), so a passing
smoke proves the path the solver will actually take.

Decode-only (no OCIO) is supported via --no-ocio. Otherwise --working-space is
forwarded to forge_io.read; use --source-cs when the file declares unknown
colorspace but you know the encoding (OCIO assume_source).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


_CONTAINER_EXTS = frozenset((".mp4", ".mov", ".mxf", ".avi", ".mkv"))
# Only single-file raw clips (one file per clip). .ari/.arx are sequence-style
# and dispatch through the sequence path so resolve_pattern can map frame_idx
# to the right per-frame file.
_RAW_CLIP_EXTS = frozenset((".r3d",))


def _ocio_debug_lines() -> list[str]:
    raw = os.environ.get("OCIO")
    if not raw:
        return ["OCIO: <unset>"]
    p = Path(raw).expanduser()
    name = p.name if p.is_file() else raw
    return [f"OCIO: {raw}", f"OCIO config basename: {name}"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="forge-align extractor smoke (mirrors the solver read path)"
    )
    parser.add_argument(
        "--plate",
        required=True,
        help="Image path, sequence pattern (printf / Flame brackets / literal), or video container",
    )
    parser.add_argument("--frame", type=int, default=1, help="Frame index to read")
    parser.add_argument(
        "--working-space",
        default="sRGB",
        help='OCIO destination colorspace (default: "sRGB"). Ignored with --no-ocio.',
    )
    parser.add_argument(
        "--source-cs",
        default="",
        help="Optional assume_source when file colorspace is unknown",
    )
    parser.add_argument(
        "--no-ocio",
        action="store_true",
        help="Decode only: pass working_space=None (no OCIO transform)",
    )
    parser.add_argument(
        "--container-fps",
        type=float,
        default=23.976,
        help="Frame rate for container seek (default: 23.976; ignored for sequences)",
    )
    args = parser.parse_args()

    for line in _ocio_debug_lines():
        print(line)

    try:
        from forge_cv.extractor import (
            extract_container_frame,
            read_raw_clip_frame,
            read_sequence_frame,
        )
        from forge_io.exceptions import ForgeIOError
    except ImportError as e:
        print(f"import failed: {e}", file=sys.stderr)
        return 1

    ws = None if args.no_ocio else args.working_space
    assume = args.source_cs or None
    ext = os.path.splitext(args.plate)[1].lower()
    is_raw_clip = ext in _RAW_CLIP_EXTS
    is_container = ext in _CONTAINER_EXTS

    try:
        if is_raw_clip:
            px = read_raw_clip_frame(
                args.plate,
                args.frame,
                working_space=ws,
                assume_source=assume,
            )
        elif is_container:
            px = extract_container_frame(
                args.plate,
                args.frame,
                fps=args.container_fps,
                working_space=ws,
                assume_source=assume,
            )
        else:
            px = read_sequence_frame(
                args.plate,
                args.frame,
                working_space=ws,
                assume_source=assume,
            )
    except ForgeIOError as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    kind = "raw_clip" if is_raw_clip else ("container" if is_container else "sequence")
    print("path_kind:", kind)
    print("shape:", px.shape)
    print("dtype:", px.dtype)
    print("mean:", float(px.mean()))
    print("min:", float(px.min()), "max:", float(px.max()))

    # Header-only metadata via forge_io for diagnostics. Sequence path goes
    # through resolve_pattern; raw clip is the literal path; container would
    # need ffprobe (skipped here).
    if not is_container:
        try:
            from forge_io import read_metadata

            if is_raw_clip:
                resolved = args.plate
            else:
                from forge_io import resolve_pattern
                resolved = resolve_pattern(args.plate, args.frame)
            meta = read_metadata(resolved)
            print("source_colorspace:", getattr(meta, "source_colorspace", "?"))
            print("bit_depth:", getattr(meta, "bit_depth", "?"))
            print("resolution:", getattr(meta, "resolution", "?"))
        except Exception as e:
            print(f"(metadata probe skipped: {type(e).__name__}: {e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
