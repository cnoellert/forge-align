"""CV alignment solver — SIFT keypoints, configurable transform model."""

import math
from typing import Optional, Tuple

import cv2
import numpy as np

from .types import AffineTransform

# Valid solve modes
MODES = ("similarity", "affine", "homography")


def solve_alignment(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    frame_index: int = 0,
    max_features: int = 10000,
    match_ratio: float = 0.75,
    ransac_thresh: float = 3.0,
    mode: str = "similarity",
) -> AffineTransform:
    """Compute the transform that maps frame_b onto frame_a.

    Uses SIFT feature detection at each image's native resolution.

    Args:
        frame_a: Reference frame (float32 RGB, [0,1]).
        frame_b: Source frame to align (float32 RGB, [0,1]).
        frame_index: Frame number for the result.
        max_features: Maximum SIFT features to detect.
        match_ratio: Lowe's ratio test threshold.
        ransac_thresh: RANSAC reprojection threshold in pixels.
        mode: Transform model to fit:
            "similarity"  — uniform scale + rotation + translation (4 DOF)
            "affine"      — non-uniform scale + shear + rotation + translation (6 DOF)
            "homography"  — full perspective (8 DOF)

    Returns:
        AffineTransform with the computed values.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    gray_a = _to_gray_uint8(frame_a)
    gray_b = _to_gray_uint8(frame_b)

    sift = cv2.SIFT_create(nfeatures=max_features)
    kp_a, desc_a = sift.detectAndCompute(gray_a, None)
    kp_b, desc_b = sift.detectAndCompute(gray_b, None)

    if desc_a is None or desc_b is None or len(kp_a) < 4 or len(kp_b) < 4:
        return _identity_transform(frame_index, confidence=0.0)

    bf = cv2.BFMatcher(cv2.NORM_L2)
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

def _to_gray_uint8(img: np.ndarray) -> np.ndarray:
    """Convert float32 RGB image to uint8 grayscale.

    Applies sRGB gamma if the image appears to be linear (low mean value),
    which is common for EXR/ACEScg footage.
    """
    if len(img.shape) == 2:
        gray = img
    elif img.shape[2] == 1:
        gray = img[:, :, 0]
    else:
        gray = 0.2989 * img[:, :, 0] + 0.5870 * img[:, :, 1] + 0.1140 * img[:, :, 2]

    if gray.dtype == np.float32 or gray.dtype == np.float64:
        # Apply sRGB gamma if image looks linear (mean < 0.2 suggests linear EXR)
        if gray.mean() < 0.2:
            gray = np.clip(gray, 0.0, 1.0)
            gray = np.where(gray <= 0.0031308,
                            gray * 12.92,
                            1.055 * np.power(gray, 1.0 / 2.4) - 0.055)
        gray = np.clip(gray * 255, 0, 255).astype(np.uint8)
    return gray


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
