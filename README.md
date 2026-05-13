# forge-align

Computer vision alignment tool for Autodesk Flame. Matches plate segments to a reference using feature detection and creates Action effects with scale/position/rotation keyframes.

## Demo

[![FORGE CV Align Demo](https://img.youtube.com/vi/H9nHyEsL_2k/0.jpg)](https://youtu.be/H9nHyEsL_2k)

## Features

- **Auto-detect** plate vs reference by resolution
- **Multi-segment batch** — select many plates + one ref, aligns all
- **Three detectors** — SIFT (default), AKAZE, SuperPoint+LightGlue
- **Timewarp-aware** — correct source frame mapping for Speed and Timing (Frame) mode timewarps
- **Colourspace-aware** — sequence frames are read through **forge-io** (OpenImageIO + OpenColorIO): defaults map to **`sRGB`** for feature detection; pass **`--source-cs` / `--ref-cs`** so unknown file colorspaces resolve via OCIO `assume_source`. Set the **`OCIO`** environment (or extend the CLI later with an explicit config path) to match your facility config.
- **Container + sequence sources** — EXR/DPX frame sequences and ProRes MOV/MXF containers
- **Three solve modes** — Similarity (4 DOF), Affine (6 DOF), Homography (8 DOF)
- **Frame sampling** — First frame, First + Last, or Every N frames
- **Round to 0.5** — optional rounding for cleaner manual tweaking
- **Confidence gate** — skips low-quality matches automatically
- **PySide settings dialog** — ref selector, detector, mode, frame sampling, rounding in one place

## Requirements

- Autodesk Flame 2025+
- Miniconda or Anaconda
- ffmpeg (for MP4/MOV reference extraction)
- **OpenImageIO + OpenColorIO** (pulled in by the **forge-io** dependency; Conda/ASWF-style stacks satisfy them). For plate/reference **sequences**, the solver expects a usable **`OCIO`** config (see forge-io `read(..., working_space=\"sRGB\")`).

## Install

```bash
git clone https://github.com/cnoellert/forge-align.git
cd forge-align
bash install.sh
```

The installer will:
1. Create (or reuse) a conda environment with Python 3.11
2. Install OpenCV, NumPy, and **forge-io** (pinned from git tag `v0.2.2` — push tags to GitHub before installing on a fresh machine)
3. Optionally install SuperPoint support (torch + lightglue, ~2 GB)
4. Install ffmpeg via conda-forge
5. Save the conda Python path to `~/.forge/config.yaml`
6. Deploy the hook globally (default) or to a custom path

You can also specify options directly:
```bash
# Install with all defaults (interactive prompts)
bash install.sh

# Deploy to a specific project in addition to global
bash install.sh --global --project /mnt/server/projects/my_project

# Specify conda env name
bash install.sh --env myenv

# Fast redeploy (skip env/pip setup)
bash install.sh --deploy-only
```

After install, restart Flame or evict the cached module:
```python
import sys; [sys.modules.pop(k) for k in list(sys.modules) if 'forge_cv_align' in k]
# Then: Rescan Python Hooks
```

## Validation

Quick **forge-io** read smoke (uses `read_frame` the same way the solver path does). Requires a generated or production plate and a Python env where **forge-io** + **OpenImageIO** import (e.g. the `forge` conda env after `install.sh`).

**Decode only** (no OCIO; good for a tiny EXR after you run `python tests/fixtures/generate_fixtures.py` in the **forge-io** repo):

```bash
python scripts/smoke_v0_1_1.py \
  --plate /absolute/path/to/forge-io/tests/fixtures/solid_rgb.exr \
  --frame 1 \
  --no-ocio
```

**With OCIO** (default working space name is passed through; set `OCIO` to your facility config first):

```bash
export OCIO=/absolute/path/to/config.ocio
python scripts/smoke_v0_1_1.py \
  --plate "/shots/plate.[0001-1000].exr" \
  --frame 42 \
  --working-space sRGB \
  --source-cs ACEScg
```

Exit code **0** on success; **1** on failure, with the error on stderr.

## Uninstall

```bash
cd forge-align
bash uninstall.sh
```

Removes the hook, pip package, and config. Optionally removes the conda environment.

## Usage

1. Open a timeline in Flame
2. Select 2 or more segments (plates + reference)
3. Right-click → **FORGE → Transforms → CV Align**
4. In the dialog: confirm reference, choose solve mode and frame sampling
5. Click **Align**

The tool creates Action effects on each plate segment with computed transforms.

## Colourspace Handling

The hook reads each segment's colourspace from Flame via `get_colour_space()` and passes it to the solver. Before feature detection, each image is converted to a consistent display-referred grayscale:

| Source Colourspace | Transfer |
|---|---|
| Linear (ACEScg, ACES2065-1, scene-linear) | sRGB gamma (OETF) |
| Log (ARRI LogC3, REDLog3G10, Sony S-Log3) | Log decode → linear → sRGB |
| Display (Rec.709, sRGB) | Passthrough |
| Unknown | Heuristic (mean < 0.2 → assume linear) |

This ensures SIFT gets usable contrast regardless of whether the source is linear EXR, log-encoded camera footage, or an sRGB offline reference.

## Detectors

| Detector | Best for | Notes |
|---|---|---|
| **SIFT** (default) | Most cases — robust, high confidence | No extra deps |
| **AKAZE** | Matched-resolution, low-contrast shots | No extra deps, binary descriptors |
| **SuperPoint** | Large scale gaps, cross-appearance matching | Requires torch + lightglue (~2 GB), optional install |

## Timewarp Support

The hook detects timewarps on source segments and maps record frames to source frames using the correct Flame API. Both methods take 1-based segment-relative frames and return timing values 1-based from source start including handles.

- **Speed mode** — `get_speed_timing()` for source frame lookup
- **Timing (Frame) mode** — `get_timing()` for source frame lookup

```
disk_frame = int(src_in - head + (timing_val - 1) - frame_offset)
```

Flame floors fractional timing values (`int()` not `round()`). Segments without timewarps use a direct record-to-source offset.

## How It Works

The hook runs inside Flame's Python environment. The actual CV solve runs in a separate conda Python process (via subprocess) to avoid conflicts between OpenCV and Flame's interpreter.

```
Flame Python (hook)
  → read segment colourspace, timewarp mode, frame mapping
  → subprocess → conda Python (forge_cv)
    → feature matching (SIFT/AKAZE/SuperPoint, colourspace-corrected grayscale)
    → geometric solve (similarity/affine/homography)
    → optional round-to-0.5
    → Action keyframe values
  ← JSON result
Flame Python (hook)
  → create/update Action effect
  → inject keyframes into .action file
```

## Troubleshooting

- **Log file:** `/tmp/forge_cv_align.log` — contains frame numbers, subprocess commands, and errors
- **"No match"** — the detector couldn't find enough features. Try a different detector (SuperPoint for large scale gaps), or Affine/Homography mode. Images with very little texture (solid backgrounds, heavy motion blur) may not match.
- **Camera raw formats** (ARX, R3D, BRAW) — cannot be read outside Flame. Use the graded or comp version on another track instead.
- **Timewarp error** — if you see `RuntimeError: This method is only available when using the Speed/Timing mode`, the hook may need to be updated. Pull the latest and redeploy.
- **ffmpeg errors** — ensure ffmpeg is installed and accessible. Required for MP4/MOV reference extraction.
- **Wrong conda Python** — check `~/.forge/config.yaml` points to the correct Python path. Re-run `bash install.sh` to update.
