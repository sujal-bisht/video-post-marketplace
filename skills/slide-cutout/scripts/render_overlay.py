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
        [--diameter-pct 22]            outer circle width as % of frame width (8-30)
        [--border-px 6] [--border-color "#FFFFFF"]
        [--position bottom-right|bottom-left|top-right|top-left]
        [--margin-pct 2.5] [--crop-offset-x 0]
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


DIAMETER_MIN_PCT, DIAMETER_MAX_PCT = 8.0, 30.0


def resolve_geometry(frame_w, frame_h, diameter_pct, margin_pct, position):
    """Work out the circle's size and corner, in pixels, from percentages.

    Percentages, not pixels, because a fixed pixel margin does not mean the same
    thing at two resolutions: 48px is 2.5% of a 1920-wide frame but only 1.25% of
    a 3840-wide one, so the same numbers produced a different-looking result on
    4K footage. Everything here scales with the frame.

    The diameter is clamped: a cutout is a corner element, and a run once came
    back at 36% of frame width sitting mid-right, which is not the look anyone
    asked for. Clamping makes that shape impossible rather than merely unlikely.
    """
    pct = min(max(diameter_pct, DIAMETER_MIN_PCT), DIAMETER_MAX_PCT)
    clamped = pct != diameter_pct
    outer = int(round(frame_w * pct / 100.0))
    if outer % 2:
        outer += 1
    margin = int(round(frame_w * margin_pct / 100.0))

    # The circle plus its margin must fit; shrink the margin before the circle.
    if outer + 2 * margin > min(frame_w, frame_h):
        margin = max(0, (min(frame_w, frame_h) - outer) // 2)

    x, y = corner_xy(position, frame_w, frame_h, outer, margin)
    return outer, margin, x, y, clamped


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


def measure_opaque_box(path):
    """Bounding box of the non-transparent pixels in a rendered overlay.

    The renderer once produced a circle at 36% of frame width with 390px margins
    while the parameters said 22% and 48px, and nothing noticed because nothing
    looked at the file. Measuring the result closes that gap: the alpha channel
    is extracted, scaled down for speed, and the opaque region's box read back.
    Returns (x0, y0, x1, y1) in full-resolution pixels, or None.
    """
    import tempfile
    scale = 10
    with tempfile.TemporaryDirectory() as tmp:
        pgm = os.path.join(tmp, "a.pgm")
        r = subprocess.run(["ffmpeg", "-y", "-i", path,
                            "-vf", "alphaextract,scale=iw/%d:ih/%d" % (scale, scale),
                            "-frames:v", "1", "-update", "1", "-pix_fmt", "gray", pgm],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 or not os.path.isfile(pgm):
            return None
        data = open(pgm, "rb").read()

    fields, i = [], 0
    while len(fields) < 4:
        while data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b"#":
            NEWLINE = bytes([10])
            while data[i:i + 1] != NEWLINE:
                i += 1
            continue
        j = i
        while not data[j:j + 1].isspace():
            j += 1
        fields.append(data[i:j])
        i = j
    i += 1
    w, h = int(fields[1]), int(fields[2])
    px = data[i:i + w * h]

    xs, ys = [], []
    for y in range(h):
        row = px[y * w:(y + 1) * w]
        hit = [x for x, v in enumerate(row) if v > 128]
        if hit:
            ys.append(y)
            xs.extend((hit[0], hit[-1]))
    if not xs:
        return None
    return min(xs) * scale, min(ys) * scale, max(xs) * scale, max(ys) * scale


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
    ap.add_argument("--margin-pct", type=float, default=2.5,
                    help="Gap from the frame edge, as a percentage of frame width. A percentage "
                         "rather than pixels so the look is identical at 1080p and 4K -- a fixed "
                         "48px margin is 2.5%% of a 1920 frame but 1.25%% of a 3840 one.")
    ap.add_argument("--margin-px", type=int, default=None,
                    help="Override the margin in absolute pixels. Prefer --margin-pct; this "
                         "exists for one-off tweaks and does not scale with resolution.")
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
    margin_pct = args.margin_pct
    if args.margin_px is not None:
        margin_pct = 100.0 * args.margin_px / frame_w
    outer, margin, pos_x, pos_y, clamped = resolve_geometry(
        frame_w, frame_h, args.diameter_pct, margin_pct, args.position)
    if clamped:
        print("note: diameter clamped into %g-%g%% of frame width; a corner cutout larger "
              "than that stops reading as a corner cutout."
              % (DIAMETER_MIN_PCT, DIAMETER_MAX_PCT), file=sys.stderr)

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

    # Check the file rather than trusting the parameters. A render once came out
    # at 36% of frame width in the middle-right while the arguments said 22% at
    # the bottom-right, and nothing caught it because nothing looked.
    box = measure_opaque_box(args.output_mov)
    if box is None:
        raise SystemExit("Rendered overlay has no opaque pixels -- the mask did not work.")
    x0, y0, x1, y1 = box
    got_d = max(x1 - x0, y1 - y0)
    tol = max(0.02 * frame_w, 24)
    problems = []
    if abs(got_d - outer) > tol:
        problems.append("diameter %dpx, expected %dpx" % (got_d, outer))
    if abs(x0 - pos_x) > tol or abs(y0 - pos_y) > tol:
        problems.append("top-left at (%d,%d), expected (%d,%d)" % (x0, y0, pos_x, pos_y))
    if problems:
        raise SystemExit("Overlay geometry is wrong: %s. Refusing to hand over a cutout that "
                         "does not match the requested look." % "; ".join(problems))

    if not args.quiet:
        size_mb = os.path.getsize(args.output_mov) / 1e6
        print("Wrote %s (%.1f MB)" % (args.output_mov, size_mb))
        print("  verified: %dpx circle (%.1f%% of width) at (%d,%d), margin %dpx"
              % (got_d, 100.0 * got_d / frame_w, x0, y0, margin))


if __name__ == "__main__":
    main()
