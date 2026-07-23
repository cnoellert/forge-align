"""Frame extraction from image sequences and video containers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

from forge_io import read, resolve_pattern


def _resolve_bin(name: str) -> str:
    """Resolve a binary from the same prefix as the running Python."""
    env_bin = os.path.join(os.path.dirname(sys.executable), name)
    if os.path.exists(env_bin):
        return env_bin
    return name  # fall back to system PATH


def _ensure_ffmpeg_env() -> None:
    """Point forge-io at the env-local ffmpeg/ffprobe before a container decode.

    forge-io v0.4.0 discovers ffmpeg via ``FORGE_FFMPEG_PATH`` /
    ``FORGE_FFPROBE_PATH`` else ``shutil.which`` (PATH only). That misses the
    split-runtime case where the solver runs under the conda env's Python but
    the env ``bin/`` isn't on ``PATH``. We resolve the binaries relative to
    ``sys.executable`` — the same trick the old direct-shell path used — and
    export them so forge-io's env-var branch finds them regardless of how we
    were launched (Flame hook, direct ``cli_solve``, or smoke). This is why the
    resolution lives at the forge-io call boundary and not only in the hook.
    Existing env values win, so a shell override still takes effect.
    """
    for var, name in (("FORGE_FFMPEG_PATH", "ffmpeg"), ("FORGE_FFPROBE_PATH", "ffprobe")):
        if os.environ.get(var):
            continue
        resolved = _resolve_bin(name)
        if resolved != name and os.path.exists(resolved):
            os.environ[var] = resolved


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


def read_container_frame(
    container_path: str,
    frame_index: int,
    *,
    working_space: str | None = "sRGB",
    assume_source: str | None = None,
    ocio_config: str | Path | None = None,
) -> np.ndarray:
    """Read a frame from an editorial/delivery container via forge-io.

    For ``.mov`` / ``.mp4`` / ``.m4v`` / ``.avi`` / ``.mkv``, forge-io v0.4.0+
    owns the ffmpeg decode: ``read(path, frame_index=N)`` selects frame ``N`` by
    **frame number** (decode-from-head ``select='gte(n,N)'``), which is exact for
    long-GOP codecs and fractional rates — no caller-supplied fps, and no ±1
    drift from input-side time seeking. Editorial containers report
    ``source_colorspace="unknown"``, so ``assume_source`` (Flame's CS) drives the
    ``working_space`` transform.

    Includes ``.mxf``: forge-io v0.5.0+ content-classifies MXF essence with
    ffprobe (editorial → ffmpeg, ARRIRAW-in-MXF → ART-CMD, Sony X-OCN →
    ``UnsupportedFileError``), so ``.mxf`` reads through this same path.

    forge-io discovers ffmpeg/ffprobe on ``PATH`` or via ``FORGE_FFMPEG_PATH`` /
    ``FORGE_FFPROBE_PATH``. :func:`_ensure_ffmpeg_env` resolves the conda-env
    binaries relative to ``sys.executable`` and exports those vars first, so the
    decode works even when the env ``bin/`` isn't on ``PATH`` (mirrors the old
    direct-shell resolution, covering hook / direct / smoke entry points alike).

    Args:
        container_path: Path to the video file.
        frame_index: 0-based frame number to read.
        working_space: OCIO destination space, or ``None`` to disable transforms.
        assume_source: Passed through when the file declares ``unknown`` colorspace.
        ocio_config: Optional explicit OCIO config path (else ``OCIO`` env).

    Returns:
        Frame as float32 RGB array, shape (H, W, 3).
    """
    if not os.path.exists(container_path):
        raise FileNotFoundError(f"Container not found: {container_path}")

    _ensure_ffmpeg_env()
    img = read(
        container_path,
        working_space=working_space,
        assume_source=assume_source,
        ocio_config=ocio_config,
        frame_index=frame_index,
    )
    return np.ascontiguousarray(img.pixels, dtype=np.float32)


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
