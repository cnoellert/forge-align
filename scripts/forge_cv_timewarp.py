"""FORGE CV Timewarp — temporal match source frames to a reference via NCC.

Flame timeline hook: right-click segments → FORGE → CV Timewarp Match.
Select one retimed source segment + one reference segment.
Finds matching source frames by NCC similarity search and writes
keyframes back to the existing Timewarp segment effect.
"""

import datetime
import os
import shutil
import sys
import tempfile
import traceback

LOG_PATH = "/tmp/forge_cv_align.log"


def _log(msg):
    line = f"{datetime.datetime.now().isoformat()}  [TW] {msg}\n"
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line)
    except Exception:
        pass
    print(f"FORGE cv_timewarp: {msg}")


# ---------------------------------------------------------------------------
# Forge python path
# ---------------------------------------------------------------------------

def _resolve_forge_python():
    config_path = os.path.expanduser("~/.forge/config.yaml")
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("conda_python:"):
                        p = line.split(":", 1)[1].strip()
                        if p and os.path.exists(p):
                            return p
        except Exception:
            pass
    for candidate in [
        os.path.expanduser("~/miniconda3/envs/forgeTest/bin/python"),
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
            "name": "CV Timewarp Match",
            "hierarchy": ["FORGE"],
            "order": 6,
            "actions": [
                {
                    "name": "CV Timewarp Match",
                    "order": 0,
                    "isVisible": _scope_timewarp_match,
                    "execute": _timewarp_match_dispatch,
                    "minimumVersion": "2025",
                },
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Scoping — need exactly 2 segments, source must have a Timewarp
# ---------------------------------------------------------------------------

def _scope_timewarp_match(selection):
    import flame
    if not selection:
        return False
    segments = [s for s in selection if isinstance(s, flame.PySegment)]
    return len(segments) >= 2


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _timewarp_match_dispatch(selection):
    import flame
    try:
        segments = [s for s in selection if isinstance(s, flame.PySegment)]
        if len(segments) != 2:
            flame.messages.show_in_dialog(
                title="FORGE CV Timewarp Match",
                message="Select exactly 2 segments:\nthe retimed source + the reference.",
                type="error", buttons=["Ok"],
            )
            return

        # Pick order: first selected = source (must have TW), second = reference
        # If ambiguous, prefer the one with a Timewarp as source
        seg_a, seg_b = segments[0], segments[1]
        a_has_tw = any(fx.type == "Timewarp" for fx in seg_a.effects)
        b_has_tw = any(fx.type == "Timewarp" for fx in seg_b.effects)

        if a_has_tw and not b_has_tw:
            source_seg, ref_seg = seg_a, seg_b
        elif b_has_tw and not a_has_tw:
            source_seg, ref_seg = seg_b, seg_a
        else:
            # Both have TW or neither — use pick order (first = source)
            source_seg, ref_seg = seg_a, seg_b

        settings = _show_timewarp_dialog(source_seg, ref_seg)
        if settings is None:
            return

        _log(f"Timewarp match: source={str(source_seg.name)} "
             f"ref={str(ref_seg.name)} "
             f"every_n={settings['every_n']} window={settings['search_window']}")

        result = _run_timewarp_match(source_seg, ref_seg, settings)

        if isinstance(result, str):
            flame.messages.show_in_dialog(
                title="FORGE CV Timewarp Match",
                message=result,
                type="info", buttons=["Ok"],
            )
        else:
            n = result["keyframes"]
            score = result["min_score"]
            flame.messages.show_in_dialog(
                title="FORGE CV Timewarp Match",
                message=f"Done.\n\n{n} keyframes written\nMin similarity score: {score:.0%}",
                type="info", buttons=["Ok"],
            )

    except Exception:
        tb = traceback.format_exc()
        _log(f"Dispatch exception:\n{tb}")
        try:
            flame.messages.show_in_dialog(
                title="FORGE CV Timewarp Match — Error",
                message=f"Unexpected error:\n{tb[-500:]}",
                type="error", buttons=["Ok"],
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Dialog
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
)


def _show_timewarp_dialog(source_seg, ref_seg):
    from PySide6.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout,
        QLabel, QSpinBox, QPushButton, QFrame,
    )
    from PySide6.QtCore import Qt

    dialog = QDialog()
    dialog.setWindowTitle("FORGE — CV Timewarp Match")
    dialog.setMinimumWidth(380)
    dialog.setStyleSheet(_SS)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(12)

    header = QLabel("CV Timewarp Match")
    header.setStyleSheet("color: #E87E24; font-weight: bold; font-size: 14px;")
    layout.addWidget(header)

    src_name = str(source_seg.name)
    ref_name = str(ref_seg.name)
    subtitle = QLabel(f"Source: {src_name}\nReference: {ref_name}")
    subtitle.setStyleSheet("color: #888; font-size: 11px;")
    layout.addWidget(subtitle)

    sep = QFrame()
    sep.setFrameShape(QFrame.HLine)
    sep.setStyleSheet("color: #3a3f4f;")
    layout.addWidget(sep)

    def _row(label_text, widget):
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(label_text)
        lbl.setFixedWidth(120)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lbl.setStyleSheet("color: #888; font-size: 11px;")
        row.addWidget(lbl)
        row.addWidget(widget)
        return row

    every_n_spin = QSpinBox()
    every_n_spin.setRange(1, 200)
    every_n_spin.setValue(5)
    every_n_spin.setToolTip("Sample one ref frame every N output frames")
    layout.addLayout(_row("Sample every N frames", every_n_spin))

    window_spin = QSpinBox()
    window_spin.setRange(0, 500)
    window_spin.setValue(12)
    window_spin.setToolTip("Search ±N source frames around predicted position.\n0 = search full source range.")
    layout.addLayout(_row("Search window ±", window_spin))

    sep2 = QFrame()
    sep2.setFrameShape(QFrame.HLine)
    sep2.setStyleSheet("color: #3a3f4f;")
    layout.addWidget(sep2)

    btn_row = QHBoxLayout()
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

    run_btn = QPushButton("Match")
    run_btn.setFixedWidth(90)
    run_btn.setStyleSheet(
        "QPushButton { background: #E87E24; color: #fff; border: none; "
        "  border-radius: 3px; padding: 6px 12px; font-size: 12px; font-weight: bold; }"
        "QPushButton:hover { background: #f59035; }"
    )
    run_btn.clicked.connect(dialog.accept)
    run_btn.setDefault(True)
    btn_row.addWidget(run_btn)
    layout.addLayout(btn_row)

    if dialog.exec() != QDialog.Accepted:
        return None

    return {
        "every_n": every_n_spin.value(),
        "search_window": window_spin.value(),
    }


# ---------------------------------------------------------------------------
# Core match logic
# ---------------------------------------------------------------------------

def _get_segment_info(seg):
    """Extract timing and media info from a segment."""
    timewarp = None
    for e in seg.effects:
        if e.type == "Timewarp":
            timewarp = e
            break

    import re
    frame_offset = 0
    file_path = str(seg.file_path)
    src_in = seg.source_in.frame
    m = re.match(r'^(.*?)(\d+)(\.\w+)$', os.path.basename(file_path))
    if m:
        disk_first = int(m.group(2))
        try:
            _head = int(seg.head)
        except (ValueError, TypeError, OverflowError):
            _head = 0
        frame_offset = src_in - (disk_first + _head)

    try:
        head = int(seg.head)
    except (ValueError, TypeError, OverflowError):
        head = 0

    try:
        colourspace = str(seg.get_colour_space())
    except Exception:
        colourspace = ""

    return {
        "file_path": file_path,
        "source_in_frame": seg.source_in.frame,
        "source_out_frame": seg.source_out.frame,
        "record_in_frame": seg.record_in.frame,
        "record_out_frame": seg.record_out.frame,
        "frame_offset": frame_offset,
        "head": head,
        "timewarp": timewarp,
        "colourspace": colourspace,
    }


def _run_timewarp_match(source_seg, ref_seg, settings):
    """Run the temporal match and write keyframes. Returns summary dict or str."""
    import json, subprocess

    source_info = _get_segment_info(source_seg)
    ref_info = _get_segment_info(ref_seg)

    tw = source_info["timewarp"]
    if tw is None:
        return "Skipped: source segment has no Timewarp effect"

    rec_in  = source_info["record_in_frame"]
    rec_out = source_info["record_out_frame"]
    src_in  = source_info["source_in_frame"]
    src_out = source_info["source_out_frame"]
    frame_offset = source_info["frame_offset"]

    ref_rec_in  = ref_info["record_in_frame"]
    ref_rec_out = ref_info["record_out_frame"]

    overlap_in  = max(rec_in,  ref_rec_in)
    overlap_out = min(rec_out, ref_rec_out)
    if overlap_in > overlap_out:
        return "Skipped: no record-timeline overlap between source and reference"

    every_n = settings["every_n"]
    search_window = settings["search_window"]

    # Build sample output frames — always include first and last
    sample_records = list(range(overlap_in, overlap_out + 1, every_n))
    if not sample_records or sample_records[-1] != overlap_out:
        sample_records.append(overlap_out)
    if overlap_in not in sample_records:
        sample_records.insert(0, overlap_in)
    sample_records = sorted(set(sample_records))

    # 1-based segment frame numbers (what set_timing expects)
    seg_frames = [r - rec_in + 1 for r in sample_records]

    # Predicted on-disk source frames from existing TW curve
    src_start = src_in - frame_offset
    src_end   = src_out - frame_offset

    src_head = source_info["head"]
    predicted_disk = []
    for sf in seg_frames:
        try:
            timing_val = tw.get_timing(float(sf))
            # timing_val is 1-based from source start (including handles)
            on_disk = int(src_in - src_head + (timing_val - 1.0) - frame_offset)
        except RuntimeError:
            # Timewarp is in Speed or Duration mode — fall back to linear mapping
            t = (sf - 1) / max(1, len(seg_frames) - 1)
            on_disk = int(round(src_start + t * (src_end - src_start)))
        on_disk = max(src_start, min(src_end, on_disk))
        predicted_disk.append(on_disk)

    # Ref container frames (0-based for ffmpeg select)
    ref_head  = ref_seg.head if hasattr(ref_seg, "head") else 0
    ref_frames_0based = [ref_head + (r - ref_rec_in) for r in sample_records]

    # source_offset: maps on-disk frame → 1-based timing value
    source_offset = (src_in - 1) - frame_offset

    _log(f"source disk range [{src_start}, {src_end}], "
         f"{len(seg_frames)} sample points, window=±{search_window}")
    _log(f"seg_frames:      {seg_frames}")
    _log(f"predicted_disk:  {predicted_disk}")
    _log(f"ref_frames_0b:   {ref_frames_0based}")

    cmd = [
        _FORGE_PYTHON, "-m", "forge_cv.cli_temporal",
        "--source",           source_info["file_path"],
        "--source-start",     str(src_start),
        "--source-end",       str(src_end),
        "--ref",              ref_info["file_path"],
        "--ref-frames",       ",".join(str(f) for f in ref_frames_0based),
        "--seg-frames",       ",".join(str(f) for f in seg_frames),
        "--predicted-source", ",".join(str(f) for f in predicted_disk),
        "--window",           str(search_window),
        "--source-offset",    str(source_offset),
        "--source-cs",        source_info.get("colourspace", ""),
        "--ref-cs",           ref_info.get("colourspace", ""),
    ]

    _log(f"Subprocess: {' '.join(cmd)}")

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired:
        return "Error: temporal solve timed out (300s)"

    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        _log(f"cli_temporal failed (exit {proc.returncode}):\n{stderr[-500:]}")
        return f"Error: solver failed — check {LOG_PATH}"

    try:
        result = json.loads(stdout.strip())
    except json.JSONDecodeError:
        _log(f"Invalid JSON from solver: {stdout[:300]}")
        return "Error: invalid solver output"

    if "error" in result:
        _log(f"Solver error: {result['error']}")
        return f"Error: {result['error']}"

    matches = result.get("matches", [])
    if not matches:
        return "Error: solver returned no matches"

    min_score = min(m["score"] for m in matches)
    _log(f"Matches received: {len(matches)}, min score={min_score:.3f}")
    for m in matches:
        _log(f"  seg={m['seg_frame']}  disk={m['source_disk_frame']}  "
             f"timing={m['timing_value']:.2f}  score={m['score']:.3f}")

    if min_score < 0.3:
        return f"Skipped: similarity too low ({min_score:.0%}). Check log for details."

    _write_timewarp_keyframes(tw, matches)

    return {"keyframes": len(matches), "min_score": min_score}


def _write_timewarp_keyframes(tw, matches):
    """save_setup → inject TW_Timing keyframes → load_setup.

    All XML manipulation is inlined here — no forge_cv import needed,
    so this runs safely on Flame's main thread.
    """
    import re

    def _key_block(index, frame, value, prev_v, next_v, prev_f, next_f):
        dx = 0.25
        dy_r = (next_v - value) / (next_f - frame) * dx if next_f > frame else 0.0
        dy_l = (value - prev_v) / (frame - prev_f) * dx if frame > prev_f else dy_r
        return (
            f'<Key Index="{index}">'
            f'<Frame>{frame:.6f}</Frame><Value>{value:.6f}</Value>'
            f'<RHandle_dX>{dx:.6f}</RHandle_dX><RHandle_dY>{dy_r:.6f}</RHandle_dY>'
            f'<LHandle_dX>{-dx:.6f}</LHandle_dX><LHandle_dY>{-dy_l:.6f}</LHandle_dY>'
            f'<CurveMode>hermite</CurveMode><CurveOrder>linear</CurveOrder>'
            f'</Key>'
        )

    def _inject(xml, keyframes):
        n = len(keyframes)
        last_val = keyframes[-1][1]
        kf_parts = []
        for i, (sf, tv) in enumerate(keyframes):
            pf = keyframes[i-1][0] if i > 0 else sf
            pv = keyframes[i-1][1] if i > 0 else tv
            nf = keyframes[i+1][0] if i < n-1 else sf
            nv = keyframes[i+1][1] if i < n-1 else tv
            kf_parts.append(_key_block(i, float(sf), float(tv),
                                       float(pv), float(nv), float(pf), float(nf)))
        new_channel = (
            f'<Channel Name="Timing">'
            f'<Extrap>linear</Extrap><Value>{last_val:.6f}</Value>'
            f'<Size>{n}</Size><KeyVersion>2</KeyVersion>'
            f'<KFrames>{"".join(kf_parts)}</KFrames>'
            f'</Channel>'
        )
        result, count = re.subn(
            r'<TW_Timing>.*?</TW_Timing>',
            f'<TW_Timing>{new_channel}</TW_Timing>',
            xml, flags=re.DOTALL,
        )
        if count == 0:
            raise ValueError("TW_Timing block not found in setup XML")
        # Switch to Timing mode (TW_RetimerMode=1) so TW_Timing channel is active
        result = re.sub(r'<TW_RetimerMode>\d+</TW_RetimerMode>',
                        '<TW_RetimerMode>1</TW_RetimerMode>', result)
        return result

    temp_dir = tempfile.mkdtemp(prefix="forge_tw_")
    try:
        setup_base = os.path.join(temp_dir, "tw_match")
        tw.save_setup(setup_base)
        _log(f"Saved timewarp setup: {setup_base}")

        # Flame appends .timewarp_node to save_setup path
        xml_path = setup_base + ".timewarp_node"
        if not os.path.exists(xml_path):
            xml_path = setup_base + ".timewarp.timewarp_node"
        with open(xml_path) as f:
            xml_content = f.read()

        keyframes = [(float(m["seg_frame"]), float(m["timing_value"])) for m in matches]
        _log(f"Injecting keyframes: {keyframes}")

        modified_xml = _inject(xml_content, keyframes)
        with open(xml_path, "w") as f:
            f.write(modified_xml)

        tw.load_setup(setup_base)
        _log("Timewarp reloaded successfully")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
