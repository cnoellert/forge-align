# forge-align

Computer vision alignment tool for Autodesk Flame. Matches plate segments to a reference using SIFT feature detection and creates Action effects with scale/position/rotation keyframes.

## Features

- **Auto-detect** plate vs reference by resolution
- **Multi-segment batch** — select many plates + one ref, aligns all
- **Timewarp-aware** — correct source frame mapping for Speed and Frame mode timewarps
- **Colourspace-aware** — reads colourspace from Flame (ACEScg, ARRI LogC3, Rec.709, etc.) and applies the correct transfer function for feature detection
- **Three solve modes** — Similarity (4 DOF), Affine (6 DOF), Homography (8 DOF)
- **Frame sampling** — First frame, First + Last, or Every N frames
- **Confidence gate** — skips low-quality matches automatically
- **PySide settings dialog** — ref selector, mode, frame sampling in one place

## Requirements

- Autodesk Flame 2025+
- Miniconda or Anaconda
- ffmpeg (for MP4/MOV reference extraction)

## Install

```bash
git clone https://github.com/cnoellert/forge-align.git
cd forge-align
bash install.sh
```

The installer will:
1. Create (or reuse) a conda environment with Python 3.11
2. Install OpenCV and NumPy
3. Check for ffmpeg
4. Save the conda Python path to `~/.forge/config.yaml`
5. Deploy the hook — you choose: all projects or a single project

You can also specify options directly:
```bash
# Install hook for all Flame projects
bash install.sh --global

# Install hook for a specific project
bash install.sh --project /mnt/server/projects/my_project

# Specify conda env name
bash install.sh --env myenv --global
```

After install, rescan Python Hooks in Flame (or restart).

## Uninstall

```bash
cd forge-align
bash uninstall.sh
```

Removes the hook, pip package, and config. Optionally removes the conda environment.

## Usage

1. Open a timeline in Flame
2. Select 2 or more segments (plates + reference)
3. Right-click → **FORGE → CV Align → CV Align**
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

## Timewarp Support

The hook detects timewarps on source segments and maps record frames to source frames using the correct Flame API:

- **Speed mode** — `get_speed_timing()` for source frame lookup
- **Timing (Frame) mode** — `get_timing()` for source frame lookup

Segments without timewarps use a direct record-to-source offset.

## How It Works

The hook runs inside Flame's Python environment. The actual CV solve runs in a separate conda Python process (via subprocess) to avoid conflicts between OpenCV and Flame's interpreter.

```
Flame Python (hook)
  → read segment colourspace, timewarp mode, frame mapping
  → subprocess → conda Python (forge_cv)
    → SIFT feature matching (colourspace-corrected grayscale)
    → geometric solve (similarity/affine/homography)
    → Action keyframe values
  ← JSON result
Flame Python (hook)
  → create/update Action effect
  → inject keyframes into .action file
```

## Troubleshooting

- **Log file:** `/tmp/forge_cv_align.log` — contains frame numbers, subprocess commands, and errors
- **"No match"** — SIFT couldn't find enough features. Try Affine or Homography mode. Images with very little texture (solid backgrounds, heavy motion blur) may not match.
- **Timewarp error** — if you see `RuntimeError: This method is only available when using the Speed/Timing mode`, the hook may need to be updated. Pull the latest and redeploy.
- **ffmpeg errors** — ensure ffmpeg is installed and accessible. Required for MP4/MOV reference extraction.
- **Wrong conda Python** — check `~/.forge/config.yaml` points to the correct Python path. Re-run `bash install.sh` to update.
