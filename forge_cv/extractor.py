"""Frame extraction from image sequences and video containers."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np

from forge_io import read, resolve_pattern


def _resolve_bin(name: str) -> str:
    """Resolve a binary from the same prefix as the running Python."""
    env_bin = os.path.join(os.path.dirname(sys.executable), name)
    if os.path.exists(env_bin):
        return env_bin
    return name  # fall back to system PATH


def read_sequence_frame(
    path_pattern: str,
    frame_index: int,
    *,
    working_space: str | None = "sRGB",
    assume_source: str | None = None,
    ocio_config: str | Path | None = None,
) -> np.ndarray:
    """Read a single frame from an image sequence on disk.

    Decodes via ``forge_io`` (OpenImageIO + optional OpenColorIO). Pixels are
    returned as float32 RGB, shape (H, W, 3). Default ``working_space`` is
    ``\"sRGB\"`` so downstream feature detectors see display-referred values;
    set ``working_space=None`` to skip OCIO (scene-linear / file-native floats).

    Args:
        path_pattern: Path with frame token — printf (``%04d``), Flame-style
            range (``[0100-0200]``), a literal single-frame path, or a literal
            path with a padded frame number before the extension (``plate.0001.exr``).
        frame_index: The source frame number to read.
        working_space: OCIO destination space, or ``None`` to disable transforms.
        assume_source: Passed through when the file declares ``unknown`` colorspace.
        ocio_config: Optional explicit OCIO config path (else ``OCIO`` env).

    Returns:
        Frame as float32 RGB array, shape (H, W, 3), range typical of the chosen
        ``working_space`` (e.g. ``[0, 1]`` for ``sRGB``).
    """
    resolved = resolve_pattern(path_pattern, frame_index)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Frame not found: {resolved}")

    img = read(
        resolved,
        working_space=working_space,
        assume_source=assume_source,
        ocio_config=ocio_config,
    )
    return np.ascontiguousarray(img.pixels, dtype=np.float32)


def extract_container_frame(
    container_path: str,
    frame_index: int,
    temp_dir: Optional[str] = None,
    fps: float = 23.976,
    *,
    working_space: str | None = "sRGB",
    assume_source: str | None = None,
    ocio_config: str | Path | None = None,
) -> np.ndarray:
    """Extract a single frame from a video container (MOV/MP4) via ffmpeg.

    Uses time-based seeking (-ss before -i) for fast random access.
    ffmpeg seeks to the nearest keyframe then decodes forward to the
    exact target PTS — frame-accurate for all codecs (ProRes, H.264,
    H.265, etc.).

    Args:
        container_path: Path to the video file.
        frame_index: 0-based frame number to extract.
        temp_dir: Optional temp directory for intermediate PNG.
        fps: Container frame rate (for converting frame index to seek time).
        working_space: OCIO destination for the decoded PNG (default ``sRGB``).
        assume_source: Optional OCIO assume role for unknown sources.
        ocio_config: Optional explicit OCIO config path.

    Returns:
        Frame as float32 RGB array, shape (H, W, 3).
    """
    if not os.path.exists(container_path):
        raise FileNotFoundError(f"Container not found: {container_path}")

    cleanup = temp_dir is None
    if temp_dir is None:
        temp_dir = tempfile.mkdtemp(prefix="forge_cv_")

    out_path = os.path.join(temp_dir, f"frame_{frame_index:06d}.png")
    try:
        seek_time = frame_index / fps
        cmd = [
            _resolve_bin("ffmpeg"),
            "-y",
            "-nostdin",
            "-ss",
            f"{seek_time:.6f}",
            "-i",
            container_path,
            "-frames:v",
            "1",
            "-vsync",
            "0",
            out_path,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"ffmpeg failed (exit {result.returncode}): {stderr[-500:]}"
            )
        if not os.path.exists(out_path):
            raise RuntimeError("ffmpeg produced no output frame")

        img = read(
            out_path,
            working_space=working_space,
            assume_source=assume_source,
            ocio_config=ocio_config,
        )
        return np.ascontiguousarray(img.pixels, dtype=np.float32)
    finally:
        if cleanup:
            _cleanup_temp(temp_dir)


def extract_container_frames(
    container_path: str,
    frame_indices: list[int],
    temp_dir: Optional[str] = None,
) -> list[np.ndarray]:
    """Extract multiple frames from a container. Convenience wrapper."""
    return [
        extract_container_frame(container_path, idx, temp_dir)
        for idx in frame_indices
    ]


def read_raw_clip_frame(
    clip_path: str,
    frame_index: int,
    *,
    working_space: str | None = "sRGB",
    assume_source: str | None = None,
    ocio_config: str | Path | None = None,
) -> np.ndarray:
    """Read a frame from a single-file camera-raw clip (RED .r3d).

    For single-file clips the path is the clip itself and ``frame_index`` is
    the 0-based intra-clip frame, forwarded to forge-io's reader so the right
    inner frame is decoded (RED via REDline ``--start N --end N`` in forge-io
    v0.3.1+).

    **Not used for ARRI .ari/.arx** — those are sequence-style (one file per
    frame, frame number in the filename) and dispatch through
    :func:`read_sequence_frame` so ``resolve_pattern`` can substitute the
    frame number into the path before forge-io decodes the resulting per-
    frame file.
    """
    if not os.path.exists(clip_path):
        raise FileNotFoundError(f"Clip not found: {clip_path}")

    img = read(
        clip_path,
        working_space=working_space,
        assume_source=assume_source,
        ocio_config=ocio_config,
        frame_index=frame_index,
    )
    return np.ascontiguousarray(img.pixels, dtype=np.float32)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cleanup_temp(temp_dir: str) -> None:
    """Remove temp directory and contents."""
    import shutil

    try:
        shutil.rmtree(temp_dir)
    except OSError:
        pass
