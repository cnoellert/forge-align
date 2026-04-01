"""CV alignment solver — SIFT keypoints, configurable transform model."""

import math
from typing import Optional, Tuple

import cv2
import numpy as np

from .types import AffineTransform

# Valid solve modes
MODES = ("similarity", "affine", "homography")

# Valid detector types
DETECTORS = ("sift", "akaze")


def solve_alignment(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    frame_index: int = 0,
    max_features: int = 10000,
    match_ratio: float = 0.75,
    ransac_thresh: float = 3.0,
    mode: str = "similarity",
    detector: str = "sift",
    cs_a: str = "",
    cs_b: str = "",
) -> AffineTransform:
    """Compute the transform that maps frame_b onto frame_a.

    Args:
        frame_a: Reference frame (float32 RGB, [0,1]).
        frame_b: Source frame to align (float32 RGB, [0,1]).
        frame_index: Frame number for the result.
        max_features: Maximum features to detect (SIFT only; AKAZE uses threshold).
        match_ratio: Lowe’s ratio test threshold.
        ransac_thresh: RANSAC reprojection error threshold in pixels.
        mode: Transform model — "similarity", "affine", or "homography".
        detector: Feature detector — "sift" or "akaze".
            SIFT   — float L2 descriptors, reliable across a wide scale range.
            AKAZE  — binary M-LDB descriptors, non-linear scale space; more
                      robust on low-contrast and textureless regions.
        cs_a: Colourspace name for frame_a.
        cs_b: Colourspace name for frame_b.

    Returns:
        AffineTransform with tx/ty in native frame_a pixel coordinates.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if detector not in DETECTORS:
        raise ValueError(f"detector must be one of {DETECTORS}, got {detector!r}")

    gray_a = _to_gray_uint8(frame_a, cs_a)
    gray_b = _to_gray_uint8(frame_b, cs_b)

    # Build detector and matching norm
    if detector == "akaze":
        det = cv2.AKAZE_create()
        norm = cv2.NORM_HAMMING   # M-LDB binary descriptors
    else:
        det = cv2.SIFT_create(nfeatures=max_features)
        norm = cv2.NORM_L2

    kp_a, desc_a = det.detectAndCompute(gray_a, None)
    kp_b, desc_b = det.detectAndCompute(gray_b, None)

    if desc_a is None or desc_b is None or len(kp_a) < 4 or len(kp_b) < 4:
        return _identity_transform(frame_index, confidence=0.0)

    bf = cv2.BFMatcher(norm)
    raw_matches = bf.knnMatch(desc_b, desc_a, k=2)

    # Lowe's ratio test
    good = []
    for pair in raw_matches:
        if len(pair) == 2:
            m, n = pair
            if m.distance < match_ratio * n.distance:
                good.append(m)

    if len(good) < 4:
        return _identity_transform(frame_index, confidence=0.0)

    pts_b = np.float32([kp_b[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    pts_a = np.float32([kp_a[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    if mode == "similarity":
        return _solve_similarity(pts_b, pts_a, good, frame_index, ransac_thresh)
    elif mode == "affine":
        return _solve_affine(pts_b, pts_a, good, frame_index, ransac_thresh)
    else:
        return _solve_homography(pts_b, pts_a, good, frame_index, ransac_thresh)


# ---------------------------------------------------------------------------
# Solve modes
# ---------------------------------------------------------------------------

def _solve_similarity(pts_b, pts_a, good, frame_index, ransac_thresh):
    """Similarity: uniform scale + rotation + translation (4 DOF)."""
    M, inliers = cv2.estimateAffinePartial2D(
        pts_b, pts_a, method=cv2.RANSAC,
        ransacReprojThreshold=ransac_thresh,
    )
    if M is None:
        return _identity_transform(frame_index, confidence=0.0)

    inlier_count = int(inliers.sum()) if inliers is not None else 0
    confidence = inlier_count / len(good) if good else 0.0

    scale = math.sqrt(M[0, 0] ** 2 + M[1, 0] ** 2)
    rotation = math.degrees(math.atan2(M[1, 0], M[0, 0]))

    return AffineTransform(
        frame_index=frame_index,
        tx=M[0, 2], ty=M[1, 2],
        rotation=rotation,
        scale_x=scale, scale_y=scale,
        shear=0.0,
        confidence=confidence,
    )


def _solve_affine(pts_b, pts_a, good, frame_index, ransac_thresh):
    """Full affine: non-uniform scale + shear + rotation + translation (6 DOF)."""
    M, inliers = cv2.estimateAffine2D(
        pts_b, pts_a, method=cv2.RANSAC,
        ransacReprojThreshold=ransac_thresh,
    )
    if M is None:
        return _identity_transform(frame_index, confidence=0.0)

    inlier_count = int(inliers.sum()) if inliers is not None else 0
    confidence = inlier_count / len(good) if good else 0.0

    # Decompose 2x2 linear part
    a, b = M[0, 0], M[0, 1]
    c, d = M[1, 0], M[1, 1]

    sx = math.sqrt(a * a + c * c)
    sy = (a * d - b * c) / sx if sx > 1e-9 else 1.0
    rotation = math.degrees(math.atan2(c, a))

    cos_r = math.cos(math.atan2(c, a))
    sin_r = math.sin(math.atan2(c, a))
    shear_raw = (b * cos_r + d * sin_r) / sx if sx > 1e-9 else 0.0
    shear = math.degrees(math.atan(shear_raw))

    return AffineTransform(
        frame_index=frame_index,
        tx=M[0, 2], ty=M[1, 2],
        rotation=rotation,
        scale_x=sx, scale_y=abs(sy),
        shear=shear,
        confidence=confidence,
    )


def _solve_homography(pts_b, pts_a, good, frame_index, ransac_thresh):
    """Full homography: perspective transform (8 DOF)."""
    H, mask = cv2.findHomography(pts_b, pts_a, cv2.RANSAC, ransac_thresh)
    if H is None:
        return _identity_transform(frame_index, confidence=0.0)

    inlier_count = int(mask.sum()) if mask is not None else 0
    confidence = inlier_count / len(good) if good else 0.0

    return decompose_homography(H, frame_index, confidence)


# ---------------------------------------------------------------------------
# Decomposition
# ---------------------------------------------------------------------------

def decompose_homography(
    H: np.ndarray,
    frame_index: int = 0,
    confidence: float = 1.0,
) -> AffineTransform:
    """Decompose a 3x3 homography into affine components."""
    tx = H[0, 2] / H[2, 2] if abs(H[2, 2]) > 1e-9 else H[0, 2]
    ty = H[1, 2] / H[2, 2] if abs(H[2, 2]) > 1e-9 else H[1, 2]

    a = H[0, 0] / H[2, 2] if abs(H[2, 2]) > 1e-9 else H[0, 0]
    b = H[0, 1] / H[2, 2] if abs(H[2, 2]) > 1e-9 else H[0, 1]
    c = H[1, 0] / H[2, 2] if abs(H[2, 2]) > 1e-9 else H[1, 0]
    d = H[1, 1] / H[2, 2] if abs(H[2, 2]) > 1e-9 else H[1, 1]

    sx = math.sqrt(a * a + c * c)
    sy = (a * d - b * c) / sx if sx > 1e-9 else 1.0
    rotation = math.degrees(math.atan2(c, a))

    cos_r = math.cos(math.atan2(c, a))
    sin_r = math.sin(math.atan2(c, a))
    shear_raw = (b * cos_r + d * sin_r) / sx if sx > 1e-9 else 0.0
    shear = math.degrees(math.atan(shear_raw))

    return AffineTransform(
        frame_index=frame_index,
        tx=tx, ty=ty,
        rotation=rotation,
        scale_x=sx, scale_y=abs(sy),
        shear=shear,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_gray_uint8(img: np.ndarray, colourspace: str = "") -> np.ndarray:
    """Convert float32 RGB image to uint8 grayscale.

    Uses the colourspace name (from Flame) to apply the correct transfer
    function so SIFT gets consistent contrast regardless of source encoding.

    Supported colourspaces:
      - Linear (ACEScg, ACES2065-1, scene-linear, etc.) → sRGB gamma
      - Log (ARRI LogC3/4, REDLog3G10, Sony S-Log3, etc.) → log→linear→sRGB
      - Display-referred (Rec.709, sRGB, etc.) → no transform needed
      - Unknown/empty → heuristic fallback (mean < 0.2 → assume linear)
    """
    if len(img.shape) == 2:
        gray = img
    elif img.shape[2] == 1:
        gray = img[:, :, 0]
    else:
        gray = 0.2989 * img[:, :, 0] + 0.5870 * img[:, :, 1] + 0.1140 * img[:, :, 2]

    if gray.dtype == np.float32 or gray.dtype == np.float64:
        cs = colourspace.lower()
        transfer = _classify_colourspace(cs)

        if transfer == "linear":
            gray = _linear_to_srgb(gray)
        elif transfer == "log":
            gray = _log_to_srgb(gray, cs)
        elif transfer == "unknown":
            # Heuristic fallback: low mean suggests linear EXR
            if gray.mean() < 0.2:
                gray = _linear_to_srgb(gray)
        # "display" — already display-referred, no transform

        gray = np.clip(gray * 255, 0, 255).astype(np.uint8)
    return gray


def _classify_colourspace(cs: str) -> str:
    """Classify a colourspace name into transfer type."""
    if not cs:
        return "unknown"

    # Display-referred — already good for uint8
    display_keywords = ["rec.709", "rec709", "srgb", "bt.709", "bt709",
                        "video", "display"]
    for kw in display_keywords:
        if kw in cs:
            return "display"

    # Log-encoded
    log_keywords = ["logc", "log3g", "s-log", "slog", "log ", "cineon",
                    "redlog", "v-log", "vlog", "panlog", "filmlight"]
    for kw in log_keywords:
        if kw in cs:
            return "log"

    # Linear / scene-referred
    linear_keywords = ["acescg", "aces2065", "linear", "scene-linear",
                       "scene linear", "ap0", "ap1"]
    for kw in linear_keywords:
        if kw in cs:
            return "linear"

    return "unknown"


def _linear_to_srgb(gray: np.ndarray) -> np.ndarray:
    """Apply sRGB OETF to linear data."""
    gray = np.clip(gray, 0.0, 1.0)
    return np.where(gray <= 0.0031308,
                    gray * 12.92,
                    1.055 * np.power(np.maximum(gray, 0.0), 1.0 / 2.4) - 0.055)


def _log_to_srgb(gray: np.ndarray, cs: str) -> np.ndarray:
    """Convert log-encoded data to sRGB-like display values.

    Uses a generic log→linear→sRGB pipeline. The log decode is a
    reasonable approximation that works across common log curves
    (LogC3, REDLog3G10, S-Log3) for the purpose of feature detection.
    Exact decode isn't critical — we just need usable contrast.
    """
    # Generic log decode: approximate LogC3-style curve
    # LogC3: linear = (10^((x - 0.3855) / 0.2471) - 0.0522) / 5.555
    # This is close enough for SIFT purposes across most log curves
    if "logc" in cs or "arri" in cs:
        linear = (np.power(10.0, (gray - 0.3855) / 0.2471) - 0.0522) / 5.555
    elif "s-log" in cs or "slog" in cs:
        # S-Log3 approximate
        linear = np.power(10.0, (gray - 0.4105) / 0.2556) * 0.18 - 0.01
    elif "redlog" in cs or "log3g" in cs:
        # REDLog3G10 approximate
        linear = (np.power(10.0, gray) - 1.0) / 155.975
    else:
        # Generic log: simple power function that produces decent contrast
        linear = np.power(np.clip(gray, 0.0, 1.0), 2.2)

    return _linear_to_srgb(np.clip(linear, 0.0, 1.0))


def _identity_transform(frame_index: int, confidence: float = 0.0) -> AffineTransform:
    """Return a no-op transform (identity)."""
    return AffineTransform(
        frame_index=frame_index,
        tx=0.0, ty=0.0,
        rotation=0.0,
        scale_x=1.0, scale_y=1.0,
        shear=0.0,
        confidence=confidence,
    )
