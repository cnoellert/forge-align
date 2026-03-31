"""Data structures for forge_cv alignment pipeline."""

from dataclasses import dataclass
from typing import Tuple


@dataclass
class AffineTransform:
    """Application-agnostic transform result for one frame.

    All values are in pixel / degree units — no Flame-specific scaling.
    """
    frame_index: int        # source frame index
    tx: float               # translation X (pixels)
    ty: float               # translation Y (pixels)
    rotation: float         # degrees
    scale_x: float          # ratio (1.0 = no change)
    scale_y: float          # ratio (1.0 = no change)
    shear: float            # degrees
    confidence: float       # RANSAC inlier ratio 0.0–1.0


@dataclass
class FlameAlignRequest:
    """Everything needed to run an alignment, extracted from Flame context.

    This is the hand-off contract between the Flame hook and the CV layer.
    """
    online_media_path: str      # direct path to EXR/DPX sequence
    ref_media_path: str         # path to MP4/MOV container
    online_source_frame: int    # frame index into online sequence
    ref_source_frame: int       # frame index into ref container
    record_in: int              # timeline in-point (for keyframe timing)
    record_out: int             # timeline out-point
    frame_rate: str             # e.g. '23.976 fps'
    resolution: Tuple[int, int] # (width, height)
    mode: str                   # 'first' | 'first_last' | 'every_n'
    every_n: int = 1            # N for every_n mode
    action_setup_base: str = "" # path to save_setup() output (base, no ext)
