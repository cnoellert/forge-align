"""Extension-dispatch + kwarg-forwarding guards for the solver read path.

The regression these lock down: after forge-io v0.5.0 added .mxf decode,
`.mxf` must route to `read_container_frame` (forge-io, essence-classified),
NOT to a bespoke ffmpeg fallback (which was removed). And the container reader
must forward `frame_index` + `assume_source` into `forge_io.read` unchanged.
"""

import pytest

import forge_cv.cli_solve as cli
import forge_cv.extractor as extractor


# --- _read_frame dispatch --------------------------------------------------

# (extension, expected extractor function name)
DISPATCH_CASES = [
    (".r3d", "read_raw_clip_frame"),      # single-file raw clip
    (".mxf", "read_container_frame"),     # THE regression: forge-io, not ffmpeg fallback
    (".mov", "read_container_frame"),
    (".mp4", "read_container_frame"),
    (".m4v", "read_container_frame"),
    (".avi", "read_container_frame"),
    (".mkv", "read_container_frame"),
    (".exr", "read_sequence_frame"),      # image sequence
    (".dpx", "read_sequence_frame"),
    (".png", "read_sequence_frame"),
    ("",     "read_sequence_frame"),      # no extension → sequence
    (".MXF", "read_container_frame"),     # case-insensitive
]


@pytest.fixture
def record_reads(monkeypatch):
    """Patch the three extractor readers to record which one _read_frame calls."""
    seen = {}

    def make(name):
        def reader(path, frame_idx, *, assume_source=None, **_):
            seen["name"] = name
            seen["path"] = path
            seen["frame_idx"] = frame_idx
            seen["assume_source"] = assume_source
            return f"pixels::{name}"
        return reader

    for fn in ("read_raw_clip_frame", "read_container_frame", "read_sequence_frame"):
        monkeypatch.setattr(extractor, fn, make(fn))
    return seen


@pytest.mark.parametrize("ext,expected", DISPATCH_CASES)
def test_read_frame_dispatch(record_reads, ext, expected):
    result = cli._read_frame(f"/plates/shot{ext}", 42, assume_source="ARRI LogC4")
    assert record_reads["name"] == expected, f"{ext} routed to {record_reads['name']}"
    assert result == f"pixels::{expected}"
    # args threaded through unchanged
    assert record_reads["path"] == f"/plates/shot{ext}"
    assert record_reads["frame_idx"] == 42
    assert record_reads["assume_source"] == "ARRI LogC4"


def test_mxf_is_a_forgeio_container():
    """.mxf must be in the container set that routes to forge-io."""
    assert ".mxf" in cli._CONTAINER_EXTS


def test_ffmpeg_fallback_removed():
    """The bespoke ffmpeg-shell path is gone — forge-io owns container decode."""
    assert not hasattr(extractor, "extract_container_frame")
    assert not hasattr(cli, "_FFMPEG_CONTAINER_EXTS")


# --- read_container_frame → forge_io.read forwarding -----------------------

def test_read_container_frame_forwards_kwargs(monkeypatch, tmp_path):
    """frame_index + assume_source + default working_space reach forge_io.read."""
    np = pytest.importorskip("numpy")

    captured = {}

    class _Img:
        pixels = np.zeros((2, 2, 3), dtype=np.float32)

    def fake_read(path, *, working_space=None, assume_source=None,
                  ocio_config=None, frame_index=0):
        captured.update(
            path=path, working_space=working_space,
            assume_source=assume_source, frame_index=frame_index,
        )
        return _Img()

    # Don't touch the environment or shell out for ffmpeg during a unit test.
    monkeypatch.setattr(extractor, "_ensure_ffmpeg_env", lambda: None)
    monkeypatch.setattr(extractor, "read", fake_read)

    clip = tmp_path / "B_0006C020.mxf"
    clip.write_bytes(b"not-a-real-mxf")  # only existence is checked

    out = extractor.read_container_frame(str(clip), 713, assume_source="ARRI LogC4")

    assert captured["frame_index"] == 713          # intra-file frame forwarded
    assert captured["assume_source"] == "ARRI LogC4"
    assert captured["working_space"] == "sRGB"     # display-referred default
    assert captured["path"] == str(clip)
    assert out.shape == (2, 2, 3) and out.dtype == np.float32


def test_read_container_frame_missing_file(monkeypatch):
    monkeypatch.setattr(extractor, "_ensure_ffmpeg_env", lambda: None)
    with pytest.raises(FileNotFoundError):
        extractor.read_container_frame("/nope/missing.mxf", 0)
