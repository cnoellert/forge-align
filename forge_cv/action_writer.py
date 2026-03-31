"""Parse and modify Flame .action files to inject alignment keyframes."""

import re
from typing import Dict, List, Optional, Tuple

from .types import AffineTransform


# Channel names on axis1 that we write to
CHANNELS = ("position/x", "position/y", "rotation/z",
            "scaling/x", "scaling/y", "shearing/x")


def inject_transforms(
    action_text: str,
    transforms: List[AffineTransform],
    node_name: str = "axis1",
) -> str:
    """Inject alignment keyframes into an .action file's axis1 node.

    Finds the named node's Specifics block, then rewrites the target
    channels with keyframe data derived from the transforms.

    Args:
        action_text: Full text of a .action file.
        transforms: One or more AffineTransform results to write.
        node_name: Name of the target node (default axis1).

    Returns:
        Modified .action file text.
    """
    if not transforms:
        return action_text

    # Build channel value map per frame
    keyframes = _transforms_to_keyframes(transforms)

    # Find the axis1 node's Specifics block and rewrite channels
    result = _rewrite_node_channels(action_text, node_name, keyframes)
    return result


def to_flame_values(
    t: AffineTransform,
    plate_res: Optional[Tuple[int, int]] = None,
    output_res: Optional[Tuple[int, int]] = None,
) -> Dict[str, float]:
    """Convert an AffineTransform to Flame Action channel values.

    The solver produces a homography mapping plate pixels → ref pixels
    at their native resolutions. Action takes the plate at full raster
    and outputs at project resolution (= ref resolution).

    Action model (centre-based):
      out_x = (plate_x - plate_cx) * S/100 + action_px + out_cx
      out_y = (plate_y - plate_cy) * S/100 + out_cy - action_py  (Y-up)

    Solver model (direct pixel mapping):
      ref_x = sx * plate_x + tx

    Matching coefficients:
      S/100 = sx   →   S = sx * 100
      action_px = tx + plate_cx * S/100 - out_cx
      action_py = -(ty + plate_cy * S/100 - out_cy)

    Args:
        t: Solver result mapping plate→ref at native resolutions.
        plate_res: (width, height) of the plate. Required for cross-res.
        output_res: (width, height) of the Action output / ref.
    """
    if plate_res and output_res:
        plate_w, plate_h = plate_res
        out_w, out_h = output_res
        plate_cx, plate_cy = plate_w / 2.0, plate_h / 2.0
        out_cx, out_cy = out_w / 2.0, out_h / 2.0

        action_scale_x = t.scale_x * 100.0
        action_scale_y = t.scale_y * 100.0

        action_px = t.tx + plate_cx * t.scale_x - out_cx
        action_py = -(t.ty + plate_cy * t.scale_y - out_cy)

        return {
            "position/x": action_px,
            "position/y": action_py,
            "rotation/z": t.rotation,
            "scaling/x": action_scale_x,
            "scaling/y": action_scale_y,
            "shearing/x": t.shear,
        }

    # Same-resolution fallback
    return {
        "position/x": t.tx,
        "position/y": -t.ty,
        "rotation/z": t.rotation,
        "scaling/x": t.scale_x * 100.0,
        "scaling/y": t.scale_y * 100.0,
        "shearing/x": t.shear,
    }


def build_channel_block(
    channel_name: str,
    keyframes: List[Dict],
    indent: str = "\t\t",
) -> str:
    """Build a complete Channel block with keyframes.

    Args:
        channel_name: e.g. "position/x"
        keyframes: List of {"frame": int, "value": float}
        indent: Base indentation for the block.

    Returns:
        Formatted channel block text.
    """
    if not keyframes:
        return ""

    lines = []
    lines.append(f"{indent}Channel {channel_name}")
    lines.append(f"{indent}\tExtrapolation constant")

    # Default value is the first keyframe value
    lines.append(f"{indent}\tValue {_fmt(keyframes[0]['value'])}")

    if len(keyframes) == 1:
        # Static — single key
        lines.append(f"{indent}\tSize 1")
        lines.append(f"{indent}\tKeyVersion 2")
        lines.append(_build_key(0, keyframes[0], indent + "\t"))
    else:
        lines.append(f"{indent}\tSize {len(keyframes)}")
        lines.append(f"{indent}\tKeyVersion 2")
        for i, kf in enumerate(keyframes):
            lines.append(_build_key(i, kf, indent + "\t"))

    lines.append(f"{indent}\tUncollapsed")
    lines.append(f"{indent}\tEnd")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _transforms_to_keyframes(
    transforms: List[AffineTransform],
) -> Dict[str, List[Dict]]:
    """Convert transforms to per-channel keyframe lists."""
    result: Dict[str, List[Dict]] = {ch: [] for ch in CHANNELS}

    for t in transforms:
        vals = to_flame_values(t)
        for ch in CHANNELS:
            result[ch].append({
                "frame": t.frame_index,
                "value": vals[ch],
            })

    # Sort by frame
    for ch in CHANNELS:
        result[ch].sort(key=lambda kf: kf["frame"])

    return result


def _rewrite_node_channels(
    text: str,
    node_name: str,
    keyframes: Dict[str, List[Dict]],
) -> str:
    """Find the named node and rewrite its target channels."""
    lines = text.split("\n")
    result_lines = []
    i = 0

    # Find the node
    in_target_node = False
    in_specifics = False
    specifics_depth = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Detect start of our target node
        if stripped.startswith("Name ") and stripped == f"Name {node_name}":
            # Check previous line was "Node Axis"
            if result_lines and result_lines[-1].strip().startswith("Node Axis"):
                in_target_node = True

        if in_target_node and stripped == "Specifics":
            in_specifics = True
            result_lines.append(line)
            i += 1
            # Expect opening brace
            if i < len(lines) and lines[i].strip() == "{":
                result_lines.append(lines[i])
                specifics_depth = 1
                i += 1
                # Now process channels inside specifics
                i = _process_specifics(
                    lines, i, result_lines, keyframes, specifics_depth,
                )
                in_target_node = False
                in_specifics = False
                continue

        result_lines.append(line)
        i += 1

    return "\n".join(result_lines)


def _process_specifics(
    lines: List[str],
    start: int,
    result_lines: List[str],
    keyframes: Dict[str, List[Dict]],
    depth: int,
) -> int:
    """Process the Specifics block, rewriting target channels.

    Returns the index after the closing brace.
    """
    i = start
    replaced_channels = set()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped == "{":
            depth += 1
        elif stripped == "}":
            depth -= 1
            if depth == 0:
                result_lines.append(line)
                return i + 1

        # Check if this is a channel we want to replace
        ch_match = re.match(r'^(\s*)Channel\s+(.+)$', line)
        if ch_match and ch_match.group(2) in keyframes:
            ch_name = ch_match.group(2)
            indent = ch_match.group(1)

            # Skip the old channel block
            i = _skip_channel_block(lines, i)

            # Write the new one
            new_block = build_channel_block(ch_name, keyframes[ch_name], indent)
            result_lines.append(new_block)
            replaced_channels.add(ch_name)
            continue

        result_lines.append(line)
        i += 1

    return i


def _skip_channel_block(lines: List[str], start: int) -> int:
    """Skip past a Channel...End block. Returns index after End."""
    i = start + 1  # skip the "Channel xxx" line
    while i < len(lines):
        if lines[i].strip() == "End":
            return i + 1
        i += 1
    return i


def _build_key(index: int, kf: Dict, indent: str) -> str:
    """Build a single Key block."""
    lines = [
        f"{indent}Key {index}",
        f"{indent}\tFrame {kf['frame']}",
        f"{indent}\tValue {_fmt(kf['value'])}",
        f"{indent}\tRHandle_dX 0.25",
        f"{indent}\tRHandle_dY 0",
        f"{indent}\tLHandle_dX -0.25",
        f"{indent}\tLHandle_dY 0",
        f"{indent}\tCurveMode bezier",
        f"{indent}\tCurveOrder cubic",
        f"{indent}\tTangentMode smooth",
        f"{indent}\tEnd",
    ]
    return "\n".join(lines)


def _fmt(value: float) -> str:
    """Format a float for the .action file — clean trailing zeros."""
    if value == int(value):
        return str(int(value))
    # Up to 6 decimal places, strip trailing zeros
    s = f"{value:.6f}".rstrip("0").rstrip(".")
    return s
