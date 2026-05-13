#!/usr/bin/env python3
"""Smoke-read a plate via forge-io (for production validation; forge-align pins v0.2.2+).

Decode-only (no OCIO) is supported via --no-ocio. Otherwise --working-space is
passed to forge_io.read_frame (default: sRGB); use --source-cs when the file
declares unknown colorspace but you know the encoding (OCIO assume_source).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _ocio_debug_lines() -> list[str]:
    raw = os.environ.get("OCIO")
    if not raw:
        return ["OCIO: <unset>"]
    p = Path(raw).expanduser()
    name = p.name if p.is_file() else raw
    return [f"OCIO: {raw}", f"OCIO config basename: {name}"]


def main() -> int:
    parser = argparse.ArgumentParser(description="forge-io read_frame smoke (forge-align)")
    parser.add_argument(
        "--plate",
        required=True,
        help="Image path or sequence pattern (printf / Flame brackets / literal)",
    )
    parser.add_argument("--frame", type=int, default=1, help="Frame index for read_frame")
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
    args = parser.parse_args()

    for line in _ocio_debug_lines():
        print(line)

    try:
        import forge_io
        from forge_io.exceptions import ForgeIOError
    except ImportError as e:
        print(f"import forge_io failed: {e}", file=sys.stderr)
        return 1

    ws = None if args.no_ocio else args.working_space
    assume = args.source_cs or None

    try:
        img = forge_io.read_frame(
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

    px = img.pixels
    print("shape:", px.shape)
    print("dtype:", px.dtype)
    print("mean:", float(px.mean()))
    print("min:", float(px.min()), "max:", float(px.max()))
    print("source_colorspace:", img.source_colorspace)
    print("colorspace:", img.colorspace)
    print("bit_depth:", img.bit_depth)
    print("resolution:", img.resolution)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
