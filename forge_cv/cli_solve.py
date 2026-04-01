"""Standalone CLI for forge_cv alignment — called as a subprocess from Flame.

Outputs JSON with per-frame Action values.
"""

import argparse
import json
import os
import sys


def _read_ref_frame(ref_path, frame_idx):
    """Read a frame from the ref, dispatching container vs sequence."""
    from forge_cv.extractor import read_sequence_frame, extract_container_frame

    ext = os.path.splitext(ref_path)[1].lower()
    if ext in (".mp4", ".mov", ".mxf", ".avi"):
        return extract_container_frame(ref_path, frame_idx)
    else:
        return read_sequence_frame(ref_path, frame_idx)


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
                        choices=["sift", "akaze"])
    parser.add_argument("--ref-width", type=int, default=0,
                        help="Native width of the ref frame")
    parser.add_argument("--ref-height", type=int, default=0,
                        help="Native height of the ref frame")
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

    from forge_cv.extractor import read_sequence_frame
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
    for src_frame, ref_frame, action_frame in zip(
        source_frames, ref_frames, action_frames
    ):
        source_img = read_sequence_frame(args.source, src_frame)
        ref_img = _read_ref_frame(args.ref, ref_frame)

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

        ref_res = (args.ref_width, args.ref_height) if args.ref_width and args.ref_height else None
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
