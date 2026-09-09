"""
Render the speaker as a circular cutout on a transparent canvas.

WHY THE SHAPE IS BAKED AND THE ARRANGEMENT IS NOT
-------------------------------------------------
An interchange XML can carry clips, tracks and timings, but not an elliptical
mask -- Premiere's mask effects do not survive the format. Sending the geometry
through the XML as scale/position values is possible in principle and the least
reliably translated part of the format; if the editor ignores it, the cutout
opens filling the whole frame and needs fixing by hand on every video.

So the circle, its size, its ring and its corner live inside this file, and the
clip drops onto a timeline at 100% scale with nothing to adjust. What stays
editable is everything that actually varies per video: when each slide appears,
how long the cutout is on screen, which track things sit on. Style is decided
once; arrangement is decided per video.

The canvas is the full frame with everything outside the circle transparent, so
the clip needs no positioning at all -- it is already in the right place.

CODEC
-----
Three alpha-capable options are offered; see CODECS below for the sizes measured
on real footage. The default is ProRes 4444, which is not the smallest but is the
smoothest to scrub in an editor -- which is what these files are for.

Usage:
    python render_overlay.py <camera_video> <output.mov>
        [--start 12.5 --end 48.0]      portion of the camera to use
        [--diameter-pct 22]            outer circle width as % of frame width
        [--border-px 6] [--border-color "#FFFFFF"]
        [--position bottom-right|bottom-left|top-right|top-left]
        [--margin-px 48] [--crop-offset-x 0]
        [--codec prores4444|qtrle|png]
"""
import argparse
import os
import re
import subprocess
import sys


# Three alpha-capable options, measured on a 3s 1080p overlay:
#   prores4444  19.3 MB  - fastest to scrub in an NLE, built for alpha compositing
#   qtrle       24.9 MB  - lossless, quick to decode, biggest
#   png         14.4 MB  - smallest and lossless, but decodes slowly, so scrubbing
#                          a timeline full of it gets choppy
# The default optimises for editing comfort rather than bytes: these files exist
# to be dropped on a timeline and scrubbed, and they can be deleted after the
# final render. Overlays are large whatever the choice -- roughly a gigabyte per
# 2-3 minutes of slide time -- which is why they are only rendered for the ranges
# where a slide is actually on screen.
CODECS = {
    "prores4444": ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"],
    "qtrle": ["-c:v", "qtrle", "-pix_fmt", "argb"],
    "png": ["-c:v", "png", "-pix_fmt", "rgba"],
}


def probe(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1", path], text=True)
    vals = dict(line.split("=") for line in out.strip().splitlines() if "=" in line)
    return int(vals["width"]), int(vals["height"]), float(vals["duration"])


def parse_color(text):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", text.strip())
    if not m:
        raise argparse.ArgumentTypeError(
            "colour must be a 6-digit hex like #1E90FF (got %r)" % text)
    h = m.group(1)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def corner_xy(position, frame_w, frame_h, size, margin):
    right = frame_w - size - margin
    bottom = frame_h - size - margin
    return {
        "bottom-right": (right, bottom),
        "bottom-left": (margin, bottom),
        "top-right": (right, margin),
        "top-left": (margin, margin),
    }[position]


def build_filter(frame_w, frame_h, outer, border, colour, pos_x, pos_y, crop_offset_x):
    """crop to a square -> scale -> pad to the outer circle's box -> paint the
    circle and ring with geq -> pad out to the full frame, transparently."""
    inner = outer - 2 * border
    if inner <= 0:
        raise SystemExit("--border-px is too large for the chosen diameter.")
    r, g, b = colour
    centre = outer / 2.0
    r_outer = outer / 2.0
    r_inner = inner / 2.0
    dist = "hypot(X-%f,Y-%f)" % (centre, centre)

    # One geq pass does both jobs: inside the inner radius keep the video, in the
    # ring band paint the brand colour, outside make it transparent.
    geq = (
        "geq="
        "r='if(lte({d},{ri}),r(X,Y),{R})':"
        "g='if(lte({d},{ri}),g(X,Y),{G})':"
        "b='if(lte({d},{ri}),b(X,Y),{B})':"
        "a='if(lte({d},{ro}),255,0)'"
    ).format(d=dist, ri=r_inner, ro=r_outer, R=r, G=g, B=b)

    # The video is scaled to the OUTER box rather than the inner disc, so picture
    # covers the whole circle and the ring is painted over its edge. Scaling it to
    # the inner disc and padding out to the outer box leaves transparent padding
    # exactly where the inner radius falls on the cardinal axes, and the boundary
    # pixel then samples that padding and comes out black -- a faint dark seam at
    # four points on the rim. Covering the box removes the possibility.
    return (
        "crop=ih:ih:(iw-ih)/2+{off}:0,"
        "scale={outer}:{outer},"
        "format=rgba,"
        "{geq},"
        "pad={fw}:{fh}:{px}:{py}:color=0x00000000,"
        "format=rgba"
    ).format(off=crop_offset_x, outer=outer, geq=geq,
             fw=frame_w, fh=frame_h, px=pos_x, py=pos_y)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("camera_video")
    ap.add_argument("output_mov")
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--diameter-pct", type=float, default=22.0,
                    help="Outer circle diameter as a percentage of frame width. 22 is the "
                         "usual look for a 16:9 talking-head corner cutout.")
    ap.add_argument("--border-px", type=int, default=6)
    ap.add_argument("--border-color", type=parse_color, default="#FFFFFF",
                    help="Ring colour, 6-digit hex. Set this to the brand colour.")
    ap.add_argument("--position", default="bottom-right",
                    choices=["bottom-right", "bottom-left", "top-right", "top-left"])
    ap.add_argument("--margin-px", type=int, default=48)
    ap.add_argument("--crop-offset-x", type=int, default=0,
                    help="Shift the square crop horizontally, in source pixels. Use this when "
                         "the speaker does not sit dead centre, so the circle does not clip "
                         "them; framing is usually consistent across a shoot, so one value "
                         "serves a whole batch.")
    ap.add_argument("--codec", default="prores4444", choices=sorted(CODECS),
                    help="Alpha codec. prores4444 (default) scrubs smoothest in an NLE; "
                         "png is ~25%% smaller but slow to decode; qtrle is lossless and "
                         "fast but largest.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    frame_w, frame_h, duration = probe(args.camera_video)
    outer = int(round(frame_w * args.diameter_pct / 100.0))
    if outer % 2:
        outer += 1  # even dimensions keep the encoder and the centre maths happy
    pos_x, pos_y = corner_xy(args.position, frame_w, frame_h, outer, args.margin_px)

    vf = build_filter(frame_w, frame_h, outer, args.border_px, args.border_color,
                      pos_x, pos_y, args.crop_offset_x)

    cmd = ["ffmpeg", "-y"]
    if args.start is not None:
        cmd += ["-ss", "%.3f" % args.start]
    if args.end is not None:
        cmd += ["-to", "%.3f" % args.end]
    cmd += ["-i", args.camera_video, "-an", "-vf", vf] + CODECS[args.codec] + [args.output_mov]

    if not args.quiet:
        span = "" if args.start is None else " (%.2fs -> %.2fs)" % (args.start, args.end or duration)
        print("Rendering %dpx circle, %dpx %s ring, %s%s"
              % (outer, args.border_px,
                 "#%02X%02X%02X" % args.border_color, args.position, span))

    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        sys.stderr.write(result.stderr[-2000:])
        raise SystemExit("Overlay render failed.")

    if not args.quiet:
        size_mb = os.path.getsize(args.output_mov) / 1e6
        print("Wrote %s (%.1f MB)" % (args.output_mov, size_mb))


if __name__ == "__main__":
    main()
