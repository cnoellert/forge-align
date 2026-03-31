"""Frame extraction from image sequences and video containers."""

import glob
import os
import re
import subprocess
import tempfile
from typing import List, Optional

# Enable OpenEXR support in OpenCV before importing
os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"

import cv2
import numpy as np


def read_sequence_frame(path_pattern: str, frame_index: int) -> np.ndarray:
    """Read a single frame from an image sequence on disk.

    Args:
        path_pattern: Path with frame token — either printf-style (%04d)
            or Flame-style ([0100-0200]) or a literal single-frame path.
        frame_index: The source frame number to read.

    Returns:
        Frame as float32 RGB array, shape (H, W, 3), range [0, 1].
    """
    resolved = _resolve_sequence_path(path_pattern, frame_index)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Frame not found: {resolved}")

    img = cv2.imread(resolved, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Could not decode image: {resolved}")

    # Convert to float32 [0, 1]
    if img.dtype == np.uint8:
        img = img.astype(np.float32) / 255.0
    elif img.dtype == np.uint16:
        img = img.astype(np.float32) / 65535.0
    elif img.dtype == np.float32:
        pass  # already float32 (EXR)
    else:
        img = img.astype(np.float32)

    # BGR → RGB
    if len(img.shape) == 3 and img.shape[2] >= 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    return img


def extract_container_frame(
    container_path: str,
    frame_index: int,
    temp_dir: Optional[str] = None,
) -> np.ndarray:
    """Extract a single frame from a video container (MOV/MP4) via ffmpeg.

    Uses ffmpeg select filter with integer frame index — no timecode needed.

    Args:
        container_path: Path to the video file.
        frame_index: 0-based frame number to extract.
        temp_dir: Optional temp directory for intermediate EXR. Uses system
            temp if not provided.

    Returns:
        Frame as float32 RGB array, shape (H, W, 3), range [0, 1].
    """
    if not os.path.exists(container_path):
        raise FileNotFoundError(f"Container not found: {container_path}")

    cleanup = temp_dir is None
    if temp_dir is None:
        temp_dir = tempfile.mkdtemp(prefix="forge_cv_")

    out_path = os.path.join(temp_dir, f"frame_{frame_index:06d}.png")
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", container_path,
            "-vf", f"select=eq(n\\,{frame_index})",
            "-frames:v", "1",
            out_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"ffmpeg failed (exit {result.returncode}): {stderr[-500:]}"
            )
        if not os.path.exists(out_path):
            raise RuntimeError("ffmpeg produced no output frame")

        img = cv2.imread(out_path, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError(f"Could not decode extracted frame: {out_path}")

        img = img.astype(np.float32) / 255.0
        if len(img.shape) == 3 and img.shape[2] >= 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        return img
    finally:
        if cleanup:
            _cleanup_temp(temp_dir)


def extract_container_frames(
    container_path: str,
    frame_indices: List[int],
    temp_dir: Optional[str] = None,
) -> List[np.ndarray]:
    """Extract multiple frames from a container. Convenience wrapper."""
    return [
        extract_container_frame(container_path, idx, temp_dir)
        for idx in frame_indices
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_sequence_path(pattern: str, frame_index: int) -> str:
    """Resolve a frame path pattern to a concrete file path.

    Handles:
      - printf: /path/frame.%04d.exr  →  /path/frame.0012.exr
      - Flame:  /path/frame.[0001-0100].exr  →  /path/frame.0012.exr
      - Literal: /path/frame.0012.exr  →  /path/frame.0012.exr (passthrough)
    """
    # printf-style
    if "%" in pattern:
        return pattern % frame_index

    # Flame bracket notation [NNNN-NNNN]
    bracket = re.search(r'\[(\d+)-(\d+)\]', pattern)
    if bracket:
        pad = len(bracket.group(1))
        return re.sub(
            r'\[\d+-\d+\]',
            str(frame_index).zfill(pad),
            pattern,
        )

    # Literal path — try replacing the frame number first, fall back to as-is
    dirname = os.path.dirname(pattern)
    basename = os.path.basename(pattern)
    # Match trailing number before extension: name.NNNN.ext
    m = re.match(r'^(.*?)(\d+)(\.\w+)$', basename)
    if m:
        prefix, num_str, ext = m.groups()
        pad = len(num_str)
        resolved = os.path.join(dirname, f"{prefix}{str(frame_index).zfill(pad)}{ext}")
        if os.path.exists(resolved):
            return resolved

    return pattern


def _cleanup_temp(temp_dir: str) -> None:
    """Remove temp directory and contents."""
    import shutil
    try:
        shutil.rmtree(temp_dir)
    except OSError:
        pass
