"""CV alignment solver — feature-based alignment with multiple detectors."""

import math

import cv2
import numpy as np

from .types import AffineTransform

# Valid solve modes
MODES = ("similarity", "affine", "homography")

# Valid detector types
DETECTORS = ("sift", "akaze", "superpoint")


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
        match_ratio: Lowe’s ratio test threshold (SIFT/AKAZE only).
        ransac_thresh: RANSAC reprojection error threshold in pixels.
        mode: Transform model — "similarity", "affine", or "homography".
        detector: Feature detector — "sift", "akaze", or "superpoint".
            SIFT       — float L2 descriptors, reliable across a wide scale range.
            AKAZE      — binary M-LDB descriptors, non-linear scale space.
            SuperPoint — learned detector + LightGlue matcher. Best for
                          large scale gaps and cross-appearance matching
                          (e.g. raw plate vs graded offline). Requires
                          torch and lightglue packages.
        cs_a: Colourspace name for frame_a (legacy; frames are normalized via forge_io).
        cs_b: Colourspace name for frame_b (legacy; use ``--source-cs`` / ``--ref-cs``
            as ``assume_source`` on read for unknown file colorspaces).

    Returns:
        AffineTransform with tx/ty in native frame_a pixel coordinates.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if detector not in DETECTORS:
        raise ValueError(f"detector must be one of {DETECTORS}, got {detector!r}")

    # Seed OpenCV RNG for deterministic RANSAC results
    cv2.setRNGSeed(42)

    gray_a = _to_gray_uint8(frame_a, cs_a)
    gray_b = _to_gray_uint8(frame_b, cs_b)

    # SuperPoint+LightGlue: learned features, separate matching pipeline
    if detector == "superpoint":
        return _solve_superpoint(
            gray_a, gray_b, frame_index, ransac_thresh, mode)

    # OpenCV detectors: SIFT or AKAZE
    if detector == "akaze":
        det = cv2.AKAZE_create()
        norm = cv2.NORM_HAMMING
    else:
        det = cv2.SIFT_create(nfeatures=max_features)
        norm = cv2.NORM_L2

    kp_a, desc_a = det.detectAndCompute(gray_a, None)
    kp_b, desc_b = det.detectAndCompute(gray_b, None)

    if desc_a is None or desc_b is None or len(kp_a) < 4 or len(kp_b) < 4:
        return _identity_transform(frame_index, confidence=0.0)

    bf = cv2.BFMatcher(norm)
    raw_matches = bf.knnMatch(desc_b, desc_a, k=2)

    # Lowe’s ratio test
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
# SuperPoint + LightGlue
# ---------------------------------------------------------------------------

def _solve_superpoint(gray_a, gray_b, frame_index, ransac_thresh, mode):
    """Learned feature matching via SuperPoint + LightGlue.

    Much more robust than SIFT/AKAZE for large scale gaps and
    cross-appearance pairs (raw plate vs graded offline).
    """
    try:
        import torch
        from lightglue import LightGlue, SuperPoint
    except ImportError:
        raise ImportError(
            "SuperPoint detector requires torch and lightglue. "
            "Install with: pip install torch lightglue"
        )

    device = "mps" if torch.backends.mps.is_available() else "cpu"

    extractor = SuperPoint(max_num_keypoints=4096).eval().to(device)
    matcher = LightGlue(features="superpoint").eval().to(device)

    img_a = torch.from_numpy(gray_a).float()[None, None].to(device) / 255.0
    img_b = torch.from_numpy(gray_b).float()[None, None].to(device) / 255.0

    with torch.no_grad():
        feats_a = extractor.extract(img_a)
        feats_b = extractor.extract(img_b)
        result = matcher({"image0": feats_a, "image1": feats_b})

    kpts_a = feats_a["keypoints"][0].cpu().numpy()
    kpts_b = feats_b["keypoints"][0].cpu().numpy()
    matches = result["matches"][0].cpu().numpy()

    if len(matches) < 4:
        return _identity_transform(frame_index, confidence=0.0)

    mkpts_a = kpts_a[matches[:, 0]]
    mkpts_b = kpts_b[matches[:, 1]]

    pts_b = mkpts_b.reshape(-1, 1, 2).astype(np.float32)
    pts_a = mkpts_a.reshape(-1, 1, 2).astype(np.float32)
    n_matches = len(matches)

    # Wrap count in a list so _solve_* functions can use len() for confidence
    match_list = [None] * n_matches

    if mode == "similarity":
        return _solve_similarity(pts_b, pts_a, match_list, frame_index, ransac_thresh)
    elif mode == "affine":
        return _solve_affine(pts_b, pts_a, match_list, frame_index, ransac_thresh)
    else:
        return _solve_homography(pts_b, pts_a, match_list, frame_index, ransac_thresh)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_gray_uint8(img: np.ndarray, colourspace: str = "") -> np.ndarray:
    """Convert float32 RGB image to uint8 grayscale for OpenCV detectors.

    Frames are expected to already be in a display-friendly transfer (typically
    ``forge_io.read(..., working_space=\"sRGB\")``). The ``colourspace`` argument
    is kept for compatibility with ``solve_alignment(cs_a=..., cs_b=...)`` but
    is not used for transfer shaping here.
    """
    del colourspace

    if len(img.shape) == 2:
        gray = img
    elif img.shape[2] == 1:
        gray = img[:, :, 0]
    else:
        gray = 0.2989 * img[:, :, 0] + 0.5870 * img[:, :, 1] + 0.1140 * img[:, :, 2]

    if gray.dtype in (np.float32, np.float64):
        gray = np.clip(gray * 255.0, 0.0, 255.0).astype(np.uint8)
    elif gray.dtype == np.uint8:
        pass
    else:
        gray = gray.astype(np.uint8)
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
