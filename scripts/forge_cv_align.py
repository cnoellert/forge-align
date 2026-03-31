"""FORGE CV Align — align plate segments to a reference using computer vision.

Flame timeline hook: right-click segments → FORGE → CV Align.
Select one or more source plates + one reference (auto-detected by resolution).
Creates Action effects with scale/position/rotation keyframes.
"""

import datetime
import os
import re
import shutil
import sys
import tempfile
import traceback

LOG_PATH = "/tmp/forge_cv_align.log"


def _log(msg):
    line = f"{datetime.datetime.now().isoformat()}  {msg}\n"
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line)
    except Exception:
        pass
    print(f"FORGE cv_align: {msg}")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# forge conda env Python — has cv2, numpy, and forge_cv installed via pip.
# Resolved from ~/.forge/config.yaml (written by install.sh).
# Fallback: ~/miniconda3/envs/forge/bin/python
def _resolve_forge_python():
    """Read conda_python from ~/.forge/config.yaml, with fallback."""
    config_path = os.path.expanduser("~/.forge/config.yaml")
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("conda_python:"):
                        python_path = line.split(":", 1)[1].strip()
                        if python_path and os.path.exists(python_path):
                            return python_path
        except Exception:
            pass
    # Fallback: try common conda locations
    for candidate in [
        os.path.expanduser("~/miniconda3/envs/forge/bin/python"),
        os.path.expanduser("~/anaconda3/envs/forge/bin/python"),
    ]:
        if os.path.exists(candidate):
            return candidate
    return os.path.expanduser("~/miniconda3/envs/forge/bin/python")

_FORGE_PYTHON = _resolve_forge_python()


# ---------------------------------------------------------------------------
# Menu registration
# ---------------------------------------------------------------------------

def get_timeline_custom_ui_actions():
    return [
        {
            "name": "FORGE",
            "hierarchy": [],
            "actions": [],
        },
        {
            "name": "CV Align",
            "hierarchy": ["FORGE"],
            "order": 5,
            "actions": [
                {
                    "name": "CV Align",
                    "order": 0,
                    "isVisible": _scope_cv_align,
                    "execute": _cv_align_dispatch,
                    "minimumVersion": "2025",
                },
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------

def _scope_cv_align(selection):
    """Show menu when 2 or more segments are selected."""
    import flame
    segments = [s for s in selection if isinstance(s, flame.PySegment)]
    return len(segments) >= 2


# ---------------------------------------------------------------------------
# Dispatch — mode dialog + multi-segment loop
# ---------------------------------------------------------------------------

def _cv_align_dispatch(selection):
    """Entry point: show settings dialog, then align all source segments."""
    import flame

    try:
        segments = [s for s in selection if isinstance(s, flame.PySegment)]
        if len(segments) < 2:
            flame.messages.show_in_dialog(
                title="FORGE CV Align",
                message="Select at least 2 segments:\none or more source plates + one reference.",
                type="error",
                buttons=["Ok"],
            )
            return

        # Auto-detect ref: smallest resolution → find its index in original list
        smallest = min(segments, key=lambda s: s.source_width * s.source_height)
        ref_idx = segments.index(smallest)
        # If all segments are same resolution, default to last selected
        if smallest.source_width == segments[0].source_width and \
           all(s.source_width == segments[0].source_width for s in segments):
            ref_idx = len(segments) - 1

        # Show settings dialog
        settings = _show_cv_align_dialog(segments, ref_idx)
        if settings is None:
            return

        ref_seg = segments[settings["ref_index"]]
        source_segs = [s for i, s in enumerate(segments) if i != settings["ref_index"]]
        mode = settings["mode"]
        solve_frames = settings["solve_frames"]
        every_n = settings["every_n"]

        ref_name = ref_seg.name.get_value()

        # Get sequence (output) resolution
        seq = flame.timeline.clip
        out_w = seq.width
        out_h = seq.height
        ref_info = _get_segment_info(ref_seg)
        ref_base = _compute_ref_base(ref_seg)

        _log(f"CV Align: {len(source_segs)} source(s), ref={ref_name}, "
             f"mode={mode}, frames={solve_frames}, every_n={every_n}")

        # Process each source segment
        results_summary = []
        for source_seg in source_segs:
            source_name = source_seg.name.get_value()
            _log(f"--- Processing: {source_name} ---")

            try:
                result = _align_single_segment(
                    source_seg, ref_seg, ref_info, ref_base,
                    out_w, out_h, mode, solve_frames, every_n,
                )
                results_summary.append((source_name, result))
            except Exception:
                tb = traceback.format_exc()
                _log(f"Error on {source_name}:\n{tb}")
                results_summary.append((source_name, f"ERROR: {tb[-200:]}"))

        # Summary dialog
        msg = f"Reference: {ref_name}\nMode: {mode} ({solve_frames})\n\n"
        for name, result in results_summary:
            if isinstance(result, str):
                msg += f"{name}: {result}\n"
            else:
                kf_count = result.get("keyframes", 1)
                conf = result.get("min_confidence", 0)
                scale = result.get("first_scale", 0)
                msg += f"{name}: {kf_count} kf, scale={scale:.1f}%, conf={conf:.0%}\n"

        flame.messages.show_in_dialog(
            title="FORGE CV Align",
            message=msg,
            type="info",
            buttons=["Ok"],
        )

    except Exception:
        tb = traceback.format_exc()
        _log(f"_cv_align_dispatch EXCEPTION:\n{tb}")
        try:
            flame.messages.show_in_dialog(
                title="FORGE CV Align — Error",
                message=f"Unexpected error:\n{tb[-500:]}",
                type="error",
                buttons=["Ok"],
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# PySide6 settings dialog
# ---------------------------------------------------------------------------

_SS = (
    "QDialog { background: #282c34; }"
    "QLabel { color: #ccc; font-size: 12px; }"
    "QComboBox { background: #1e2028; color: #ccc; "
    "  border: 1px solid #555; border-radius: 3px; "
    "  padding: 4px 8px; font-size: 12px; }"
    "QComboBox:focus { border: 1px solid #E87E24; }"
    "QComboBox QAbstractItemView { background: #1e2028; color: #ccc; "
    "  selection-background-color: #E87E24; }"
    "QSpinBox { background: #1e2028; color: #ccc; "
    "  border: 1px solid #555; border-radius: 3px; "
    "  padding: 4px 8px; font-size: 12px; }"
    "QSpinBox:focus { border: 1px solid #E87E24; }"
    "QSpinBox:disabled { background: #16181e; color: #555; "
    "  border: 1px solid #333; }"
)


def _show_cv_align_dialog(segments, default_ref_idx):
    """Show CV Align settings dialog. Returns settings dict or None if cancelled."""
    from PySide6.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout,
        QLabel, QComboBox, QSpinBox, QPushButton, QFrame,
    )
    from PySide6.QtCore import Qt

    result = {}

    dialog = QDialog()
    dialog.setWindowTitle("FORGE — CV Align")
    dialog.setMinimumWidth(420)
    dialog.setStyleSheet(_SS)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(12)

    # ── Header ──
    header = QLabel("CV Align Settings")
    header.setStyleSheet("color: #E87E24; font-weight: bold; font-size: 14px;")
    layout.addWidget(header)

    seg_count = len(segments) - 1
    subtitle = QLabel(f"{seg_count} plate{'s' if seg_count != 1 else ''} → reference")
    subtitle.setStyleSheet("color: #888; font-size: 11px;")
    layout.addWidget(subtitle)

    # ── Separator ──
    sep1 = QFrame()
    sep1.setFrameShape(QFrame.HLine)
    sep1.setStyleSheet("color: #3a3f4f;")
    layout.addWidget(sep1)

    # ── Reference selector ──
    def _field_row(label_text, widget):
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(label_text)
        lbl.setFixedWidth(100)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lbl.setStyleSheet("color: #888; font-size: 11px;")
        row.addWidget(lbl)
        row.addWidget(widget)
        return row

    ref_combo = QComboBox()
    for seg in segments:
        name = seg.name.get_value()
        res = f"{seg.source_width}x{seg.source_height}"
        ref_combo.addItem(f"{name}  ({res})")
    ref_combo.setCurrentIndex(default_ref_idx)
    layout.addLayout(_field_row("Reference", ref_combo))

    # ── Mode selector ──
    mode_combo = QComboBox()
    mode_combo.addItems(["Similarity", "Affine", "Homography"])
    mode_combo.setCurrentIndex(0)
    layout.addLayout(_field_row("Mode", mode_combo))

    # ── Frame sampling ──
    frames_combo = QComboBox()
    frames_combo.addItems(["First Frame", "First + Last", "Every N Frames"])
    frames_combo.setCurrentIndex(0)
    layout.addLayout(_field_row("Frames", frames_combo))

    # ── Every N spinbox ──
    n_spin = QSpinBox()
    n_spin.setRange(1, 100)
    n_spin.setValue(5)
    n_spin.setEnabled(False)
    n_row = _field_row("Interval", n_spin)
    layout.addLayout(n_row)

    def _on_frames_changed(idx):
        n_spin.setEnabled(idx == 2)

    frames_combo.currentIndexChanged.connect(_on_frames_changed)

    # ── Separator ──
    sep2 = QFrame()
    sep2.setFrameShape(QFrame.HLine)
    sep2.setStyleSheet("color: #3a3f4f;")
    layout.addWidget(sep2)

    # ── Buttons ──
    btn_row = QHBoxLayout()
    btn_row.setSpacing(10)
    btn_row.addStretch()

    cancel_btn = QPushButton("Cancel")
    cancel_btn.setFixedWidth(90)
    cancel_btn.setStyleSheet(
        "QPushButton { background: #333; color: #ccc; border: 1px solid #555; "
        "  border-radius: 3px; padding: 6px 12px; font-size: 12px; }"
        "QPushButton:hover { background: #444; }"
    )
    cancel_btn.clicked.connect(dialog.reject)
    btn_row.addWidget(cancel_btn)

    align_btn = QPushButton("Align")
    align_btn.setFixedWidth(90)
    align_btn.setStyleSheet(
        "QPushButton { background: #E87E24; color: #fff; border: none; "
        "  border-radius: 3px; padding: 6px 12px; font-size: 12px; font-weight: bold; }"
        "QPushButton:hover { background: #f59035; }"
    )
    align_btn.clicked.connect(dialog.accept)
    align_btn.setDefault(True)
    btn_row.addWidget(align_btn)

    layout.addLayout(btn_row)

    # ── Execute ──
    if dialog.exec() != QDialog.Accepted:
        return None

    frames_map = {0: "first", 1: "first_last", 2: "every_n"}
    mode_map = {0: "similarity", 1: "affine", 2: "homography"}

    return {
        "ref_index": ref_combo.currentIndex(),
        "mode": mode_map[mode_combo.currentIndex()],
        "solve_frames": frames_map[frames_combo.currentIndex()],
        "every_n": n_spin.value() if frames_combo.currentIndex() == 2 else 1,
    }


# ---------------------------------------------------------------------------
# Per-segment alignment
# ---------------------------------------------------------------------------

def _align_single_segment(source_seg, ref_seg, ref_info, ref_base,
                          out_w, out_h, mode, solve_frames, every_n):
    """Align a single source segment to the reference. Returns summary dict."""

    source_info = _get_segment_info(source_seg)
    source_name = source_seg.name.get_value()

    _log(f"Source: {source_info['width']}x{source_info['height']} "
         f"@ {source_info['file_path']} "
         f"(frame_offset={source_info['frame_offset']})")

    rec_in = source_info["record_in_frame"]
    rec_out = source_info["record_out_frame"]
    seg_duration = rec_out - rec_in + 1

    # Check record range overlap with ref
    ref_rec_in = ref_info["record_in_frame"]
    ref_rec_out = ref_info["record_out_frame"]
    overlap_in = max(rec_in, ref_rec_in)
    overlap_out = min(rec_out, ref_rec_out)

    if overlap_in > overlap_out:
        _log(f"Skipping {source_name}: no record overlap with ref "
             f"(source {rec_in}-{rec_out}, ref {ref_rec_in}-{ref_rec_out})")
        return f"Skipped (no overlap with ref)"

    # Compute source frames (timewarp-aware)
    src_first = source_info["source_in_frame"]
    frame_offset = source_info["frame_offset"]
    tw = source_info.get("timewarp")

    def _source_frame_at_record(rec_frame):
        """Get on-disk source frame for a given record position, respecting timewarp.

        Flame has two timewarp modes with separate APIs:
          - Speed mode:  get_speed_timing(frame) → source timing offset
          - Timing mode: get_timing(frame) → source timing offset
        We dispatch based on tw.mode.
        """
        if tw:
            tw_mode = tw.mode
            if tw_mode == "Timing":
                timing_base = tw.get_timing(float(rec_in))
                timing_at = tw.get_timing(float(rec_frame))
            else:
                # Speed mode (default)
                timing_base = tw.get_speed_timing(float(rec_in))
                timing_at = tw.get_speed_timing(float(rec_frame))
            flame_frame = int(round(src_first + (timing_at - timing_base)))
        else:
            flame_frame = src_first + (rec_frame - rec_in)
        # Convert Flame-internal frame to on-disk frame number
        return flame_frame - frame_offset

    def _ref_frame_at_record(rec_frame):
        """Get ref container frame for a given record position."""
        return ref_base + (rec_frame - ref_info["record_in_frame"])

    if tw:
        _log(f"Timewarp detected (mode={tw.mode})")

    # Build frame pairs — clamp to overlap region with ref
    if solve_frames == "first":
        sample_records = [overlap_in]
    elif solve_frames == "first_last":
        sample_records = [overlap_in, overlap_out]
    elif solve_frames == "every_n":
        sample_records = list(range(overlap_in, overlap_out + 1, every_n))
        # Always include last frame
        if sample_records[-1] != overlap_out:
            sample_records.append(overlap_out)
    else:
        sample_records = [overlap_in]

    source_frames = [_source_frame_at_record(r) for r in sample_records]
    ref_frames = [_ref_frame_at_record(r) for r in sample_records]
    # Action keyframes: 1-based relative to segment start
    action_frames = [r - rec_in + 1 for r in sample_records]

    _log(f"Sample points: {len(source_frames)} frames")
    _log(f"  Source: {source_frames}")
    _log(f"  Ref: {ref_frames}")
    _log(f"  Action kf: {action_frames}")

    # Run CV solve
    solve_result = _run_cv_solve(
        source_info, ref_info,
        source_frames, ref_frames,
        out_w, out_h, mode,
        action_frames[0], action_frames[-1],
    )

    if solve_result is None:
        _log(f"CV solve failed for {source_name}")
        return "No match"

    # Extract results
    if "frames" in solve_result:
        frame_results = solve_result["frames"]
    else:
        frame_results = [solve_result]

    min_confidence = min(r["confidence"] for r in frame_results)
    _log(f"Solved: {len(frame_results)} kf, min confidence={min_confidence:.3f}")

    if min_confidence < 0.3:
        _log(f"Skipping {source_name}: confidence {min_confidence:.1%} below threshold")
        return f"Skipped (confidence {min_confidence:.0%})"

    # Build flame_values list
    flame_values_list = []
    for r in frame_results:
        flame_values_list.append({
            "frame_index": r.get("frame_index", 1),
            "position/x": r["position_x"],
            "position/y": r["position_y"],
            "rotation/z": r["rotation_z"],
            "scaling/x": r["scaling_x"],
            "scaling/y": r["scaling_y"],
            "shearing/x": r["shearing_x"],
        })

    # Apply Action effect — unique temp dir per segment to avoid stale data
    temp_dir = tempfile.mkdtemp(prefix="forge_cv_align_")

    try:
        _apply_action_effect(source_seg, flame_values_list, temp_dir)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return {
        "keyframes": len(frame_results),
        "min_confidence": min_confidence,
        "first_scale": frame_results[0]["scaling_x"],
    }


# ---------------------------------------------------------------------------
# Segment info extraction
# ---------------------------------------------------------------------------

def _get_segment_info(seg):
    """Extract media info from a segment."""
    timewarp = None
    for e in seg.effects:
        if e.type == "Timewarp":
            timewarp = e
            break

    # Detect frame numbering offset between Flame's source_in and on-disk files.
    # Some clips (imported footage) have source_in matching disk frame numbers.
    # Others (Flame-internal comps) have renumbered source_in — offset via start_frame.
    frame_offset = 0
    file_path = str(seg.file_path)
    if hasattr(seg, 'start_frame'):
        src_in = seg.source_in.frame
        # Test if source_in resolves to an existing file
        m = re.match(r'^(.*?)(\d+)(\.\w+)$', os.path.basename(file_path))
        if m:
            prefix, num_str, ext = m.groups()
            pad = len(num_str)
            dirname = os.path.dirname(file_path)
            test_path = os.path.join(dirname, f"{prefix}{str(src_in).zfill(pad)}{ext}")
            if not os.path.exists(test_path):
                # source_in doesn't match disk — use start_frame offset
                frame_offset = src_in - seg.start_frame

    return {
        "file_path": file_path,
        "width": seg.source_width,
        "height": seg.source_height,
        "source_in_frame": seg.source_in.frame,
        "source_out_frame": seg.source_out.frame,
        "record_in_frame": seg.record_in.frame,
        "record_out_frame": seg.record_out.frame,
        "frame_offset": frame_offset,
        "timewarp": timewarp,
    }


def _compute_ref_base(ref_seg):
    """Compute the container frame offset for the ref's record_in.

    Accounts for head frames (pre-roll) in the container.
    """
    head = ref_seg.head if hasattr(ref_seg, 'head') else 0
    return head


# ---------------------------------------------------------------------------
# CV solve
# ---------------------------------------------------------------------------

def _run_cv_solve(source_info, ref_info, source_frames, ref_frames,
                  out_w, out_h, mode, record_in=1, record_out=1):
    """Run the forge_cv alignment via subprocess."""
    import json
    import subprocess

    cmd = [
        _FORGE_PYTHON, "-m", "forge_cv.cli_solve",
        "--source", source_info["file_path"],
        "--source-frames", ",".join(str(f) for f in source_frames),
        "--ref", ref_info["file_path"],
        "--ref-frames", ",".join(str(f) for f in ref_frames),
        "--source-width", str(source_info["width"]),
        "--source-height", str(source_info["height"]),
        "--output-width", str(out_w),
        "--output-height", str(out_h),
        "--record-in", str(record_in),
        "--record-out", str(record_out),
        "--mode", mode,
    ]

    _log(f"Subprocess: {' '.join(cmd)}")

    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        _log("CV solve subprocess timed out")
        return None

    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        _log(f"CV solve failed (exit {proc.returncode})")
        if stderr:
            _log(f"  stderr: {stderr[-500:]}")
        if stdout:
            _log(f"  stdout: {stdout[-500:]}")
        return None

    try:
        result = json.loads(stdout.strip())
    except json.JSONDecodeError:
        _log(f"CV solve returned invalid JSON: {stdout[:500]}")
        if stderr:
            _log(f"  stderr: {stderr[:500]}")
        return None

    if "error" in result:
        _log(f"CV solve error: {result['error']}")
        return None

    return result


# ---------------------------------------------------------------------------
# Action effect application (MUST run on main thread)
# ---------------------------------------------------------------------------

def _apply_action_effect(source_seg, flame_values_list, temp_dir):
    """Create Action effect, inject CV keyframes, load back."""
    import json
    import subprocess

    # Use existing Action effect or create a new one
    efx = None
    for e in source_seg.effects:
        if e.type == "Action":
            efx = e
            _log(f"Reusing existing Action effect on {source_seg.name.get_value()}")
            break

    if efx is None:
        source_seg.create_effect("Action")
        for e in source_seg.effects:
            if e.type == "Action":
                efx = e
                break
        _log(f"Created Action effect on {source_seg.name.get_value()}")

    if efx is None:
        raise RuntimeError("Action effect not found after creation")

    # Save baseline setup
    setup_base = os.path.join(temp_dir, "cv_align")
    efx.save_setup(setup_base)
    _log(f"Saved baseline to {setup_base}")

    # save_setup creates either flat file or bundle directory
    action_path = setup_base + ".action"
    if os.path.isdir(action_path):
        action_file = os.path.join(action_path, "_action.action")
    else:
        action_file = action_path

    if not os.path.exists(action_file):
        raise RuntimeError(f"save_setup did not create action file at {action_file}")

    # Build transforms list for subprocess
    transforms_data = []
    for fv in flame_values_list:
        transforms_data.append({
            "frame_index": fv.get("frame_index", 1),
            "position_x": fv["position/x"],
            "position_y": fv["position/y"],
            "rotation_z": fv["rotation/z"],
            "scaling_x": fv["scaling/x"],
            "scaling_y": fv["scaling/y"],
            "shearing_x": fv.get("shearing/x", 0.0),
        })

    inject_script = (
        "import json, sys\n"
        "from forge_cv.action_writer import inject_transforms\n"
        "from forge_cv.types import AffineTransform\n"
        "data = json.loads(sys.argv[1])\n"
        "transforms = []\n"
        "for fv in data:\n"
        "    transforms.append(AffineTransform(\n"
        "        frame_index=fv['frame_index'],\n"
        "        tx=fv['position_x'], ty=-fv['position_y'],\n"
        "        rotation=fv['rotation_z'],\n"
        "        scale_x=fv['scaling_x']/100, scale_y=fv['scaling_y']/100,\n"
        "        shear=fv.get('shearing_x', 0), confidence=1.0))\n"
        "with open(sys.argv[2]) as f:\n"
        "    original = f.read()\n"
        "modified = inject_transforms(original, transforms)\n"
        "with open(sys.argv[2], 'w') as f:\n"
        "    f.write(modified)\n"
        "print('OK')\n"
    )

    _log(f"Injecting {len(transforms_data)} keyframe(s) into: {action_file}")
    proc = subprocess.run(
        [_FORGE_PYTHON, "-c", inject_script, json.dumps(transforms_data), action_file],
        capture_output=True, timeout=30,
    )

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"Action injection failed: {err[-500:]}")

    _log("Injected CV keyframes into .action file")

    # Load modified setup back
    efx.load_setup(setup_base)
    _log("Loaded modified setup back onto effect")
