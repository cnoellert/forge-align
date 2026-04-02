"""Standalone CLI for forge_cv alignment — called as a subprocess from Flame.

Outputs JSON with per-frame Action values.
"""

import argparse
import json
import os
import sys


_CONTAINER_EXTS = frozenset((".mp4", ".mov", ".mxf", ".avi", ".mkv"))


def _read_frame(path, frame_idx, fps=23.976):
    """Read a frame, dispatching container vs image sequence by extension."""
    from forge_cv.extractor import read_sequence_frame, extract_container_frame

    ext = os.path.splitext(path)[1].lower()
    if ext in _CONTAINER_EXTS:
        return extract_container_frame(path, frame_idx, fps=fps)
    else:
        return read_sequence_frame(path, frame_idx)


def main():
    parser = argparse.ArgumentParser(description="forge_cv alignment solver")
    parser.add_argument("--source", required=True, help="Source plate media path")
    parser.add_argument("--source-frames", required=True,
                        help="Comma-separated source frame indices")
    parser.add_argument("--ref", required=True, help="Reference media path")
    parser.add_argument("--ref-frames", required=True,
                        help="Comma-separated ref frame indices")
    parser.add_argument("--source-width", type=int, required=True)
    parser.add_argument("--source-height", type=int, required=True)
    parser.add_argument("--output-width", type=int, required=True)
    parser.add_argument("--output-height", type=int, required=True)
    parser.add_argument("--record-in", type=int, default=1,
                        help="First Action keyframe frame number")
    parser.add_argument("--record-out", type=int, default=1,
                        help="Last Action keyframe frame number")
    parser.add_argument("--mode", default="similarity",
                        choices=["similarity", "affine", "homography"])
    parser.add_argument("--detector", default="sift",
                        choices=["sift", "akaze", "superpoint"])
    parser.add_argument("--ref-width", type=int, default=0,
                        help="Native width of the ref frame")
    parser.add_argument("--ref-height", type=int, default=0,
                        help="Native height of the ref frame")
    parser.add_argument("--ref-fps", type=float, default=23.976,
                        help="Ref container frame rate (for seek-based extraction)")
    parser.add_argument("--source-fps", type=float, default=0,
                        help="Source container frame rate (for seek-based extraction)")
    parser.add_argument("--source-cs", default="",
                        help="Source colourspace (e.g. ACEScg, ARRI LogC3)")
    parser.add_argument("--ref-cs", default="",
                        help="Reference colourspace (e.g. Rec.709 video)")
    args = parser.parse_args()

    source_frames = [int(f) for f in args.source_frames.split(",")]
    ref_frames = [int(f) for f in args.ref_frames.split(",")]

    if len(source_frames) != len(ref_frames):
        print(json.dumps({"error": "source-frames and ref-frames must have same count"}))
        sys.exit(1)

    # Enable OpenEXR
    os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"

    from forge_cv.solver import solve_alignment
    from forge_cv.action_writer import to_flame_values

    plate_res = (args.source_width, args.source_height)
    output_res = (args.output_width, args.output_height)

    # Compute Action keyframe frame numbers
    n = len(source_frames)
    if n == 1:
        action_frames = [args.record_in]
    else:
        # Distribute evenly from record_in to record_out
        span = args.record_out - args.record_in
        action_frames = [
            args.record_in + i * span // (n - 1)
            for i in range(n)
        ]

    results = []
    ratios_logged = False
    for src_frame, ref_frame, action_frame in zip(
        source_frames, ref_frames, action_frames
    ):
        src_fps = args.source_fps if args.source_fps else 23.976
        source_img = _read_frame(args.source, src_frame, fps=src_fps)
        ref_img = _read_frame(args.ref, ref_frame, fps=args.ref_fps)

        # -----------------------------------------------------------------
        # Disk vs segment resolution correction.
        #
        # Flame's seg.source_width/height (passed as --source-width/height
        # and --ref-width/height) is the segment's working resolution,
        # which may differ from the file on disk (e.g. media resized on
        # import or published at half-res).  The solver works in disk pixel
        # space; the Action operates in segment pixel space.  We detect the
        # ratio for BOTH source and ref, then rescale the solver output.
        # -----------------------------------------------------------------
        src_disk_h, src_disk_w = source_img.shape[:2]
        ref_disk_h, ref_disk_w = ref_img.shape[:2]

        seg_w, seg_h = plate_res
        ref_seg_w = args.ref_width if args.ref_width else ref_disk_w
        ref_seg_h = args.ref_height if args.ref_height else ref_disk_h

        # Source: disk vs segment ratio
        src_rx = src_disk_w / seg_w if seg_w else 1.0
        src_ry = src_disk_h / seg_h if seg_h else 1.0

        # Ref: disk vs segment ratio
        ref_rx = ref_disk_w / ref_seg_w if ref_seg_w else 1.0
        ref_ry = ref_disk_h / ref_seg_h if ref_seg_h else 1.0

        if not ratios_logged:
            if abs(src_rx - 1.0) > 0.01 or abs(src_ry - 1.0) > 0.01:
                print(f"source: disk {src_disk_w}x{src_disk_h} vs seg {seg_w}x{seg_h}, "
                      f"ratio {src_rx:.3f}x{src_ry:.3f}", file=sys.stderr)
            if abs(ref_rx - 1.0) > 0.01 or abs(ref_ry - 1.0) > 0.01:
                print(f"ref: disk {ref_disk_w}x{ref_disk_h} vs seg {ref_seg_w}x{ref_seg_h}, "
                      f"ratio {ref_rx:.3f}x{ref_ry:.3f}", file=sys.stderr)
            ratios_logged = True

        result = solve_alignment(
            ref_img, source_img, frame_index=action_frame,
            mode=args.mode, detector=args.detector,
            cs_a=args.ref_cs, cs_b=args.source_cs,
        )

        if result.confidence <= 0.0:
            print(json.dumps({
                "error": f"No match at source frame {src_frame}",
                "confidence": 0.0,
            }))
            sys.exit(1)

        # Rescale solver output from disk pixel space → segment pixel space.
        #
        # Solver maps: disk_src_pt → disk_ref_pt
        #   ref_disk_pt = scale * src_disk_pt + tx
        #
        # In segment space: src_disk = src_seg * src_rx
        #                   ref_disk = ref_seg * ref_rx
        # So: ref_seg * ref_rx = scale * src_seg * src_rx + tx
        #     ref_seg = (scale * src_rx / ref_rx) * src_seg + tx / ref_rx
        #
        # Effective scale in seg space: scale * src_rx / ref_rx
        # Effective tx in ref-seg space: tx / ref_rx
        #
        from forge_cv.types import AffineTransform
        eff_sx = result.scale_x * src_rx / ref_rx
        eff_sy = result.scale_y * src_ry / ref_ry
        eff_tx = result.tx / ref_rx
        eff_ty = result.ty / ref_ry

        result = AffineTransform(
            frame_index=result.frame_index,
            tx=eff_tx, ty=eff_ty,
            rotation=result.rotation,
            scale_x=eff_sx, scale_y=eff_sy,
            shear=result.shear,
            confidence=result.confidence,
        )

        # ref_res for to_flame_values is the segment ref resolution (not disk)
        ref_res = (ref_seg_w, ref_seg_h) if ref_seg_w and ref_seg_h else None
        fv = to_flame_values(result, plate_res=plate_res, output_res=output_res, ref_res=ref_res)

        results.append({
            "frame_index": action_frame,
            "confidence": result.confidence,
            "position_x": fv["position/x"],
            "position_y": fv["position/y"],
            "rotation_z": fv["rotation/z"],
            "scaling_x": fv["scaling/x"],
            "scaling_y": fv["scaling/y"],
            "shearing_x": fv["shearing/x"],
        })

    # Output
    if len(results) == 1:
        print(json.dumps(results[0]))
    else:
        print(json.dumps({"frames": results}))


if __name__ == "__main__":
    main()
