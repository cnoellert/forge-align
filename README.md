# forge-align

Computer vision alignment tool for Autodesk Flame. Matches plate segments to a reference using SIFT feature detection and creates Action effects with scale/position/rotation keyframes.

## Features

- **Auto-detect** plate vs reference by resolution
- **Multi-segment batch** — select many plates + one ref, aligns all
- **Timewarp-aware** — correct source frame mapping for speed-ramped clips
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
3. Save the conda Python path for the hook to find
4. Deploy the hook to your Flame project

You can also specify options directly:
```bash
bash install.sh --env forge --project /mnt/server/projects/my_project
```

After install, rescan Python Hooks in Flame (or restart).

## Usage

1. Open a timeline in Flame
2. Select 2 or more segments (plates + reference)
3. Right-click → **FORGE → CV Align → CV Align**
4. In the dialog: confirm reference, choose solve mode and frame sampling
5. Click **Align**

The tool creates Action effects on each plate segment with computed transforms.

## How It Works

The hook runs inside Flame's Python environment. The actual CV solve runs in a separate conda Python process (via subprocess) to avoid conflicts between OpenCV and Flame's interpreter.

```
Flame Python (hook)
  → subprocess → conda Python (forge_cv)
    → SIFT feature matching
    → geometric solve (similarity/affine/homography)
    → Action keyframe values
  ← JSON result
Flame Python (hook)
  → create/update Action effect
  → inject keyframes into .action file
```
