"""
Assemble the slide timeline: render the cutout overlays, gather the media, and
write an FCP7 XML the editor can open and rearrange.

WHAT LANDS ON THE TIMELINE
--------------------------
    V3   circular cutout overlays   (transparent everywhere but the circle)
    V2   slide stills               (one clip per slide, at its own range)
    V1   the camera, full frame     (spans the whole video)
    A1   the camera audio           (spans the whole video)

Stacked that way, the speaker is full frame wherever no slide covers her, and
during a slide range the slide hides the camera while the cutout sits on top.
Nothing needs an effect applied, and every piece is an ordinary clip: the editor
can drag a slide's edge to change when it appears, delete one, swap one out, or
shorten the cutout. Only the circle's geometry is fixed, because that cannot
travel through this format.

MEDIA IS COPIED, NOT LINKED
---------------------------
Slide images are copied next to the XML rather than referenced where they
happened to live. An interchange XML holds absolute paths, so a timeline that
points at a folder the user later tidies up opens with media offline. Keeping
the media beside the XML makes the handoff self-contained -- except for the
camera file itself, which is far too large to duplicate and has to stay put.

Usage:
    python build_timeline.py <camera_video> <resolved_slides.json> <output_dir>
        [--basename NAME]
        [--diameter-pct 22] [--border-px 6] [--border-color "#1E90FF"]
        [--position bottom-right] [--margin-px 48] [--crop-offset-x 0]
        [--codec prores4444] [--handles 0.0]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import render_overlay as ovl

# scripts/ -> slide-cutout/ -> skills/ -> plugin root -> lib/
_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parents[2] / "lib"):
    if (_candidate / "fcp7xml.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
try:
    from fcp7xml import Clip, build_fcp7_xml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Cannot import the FCP7 XML writer (%s). Expected it either alongside "
        "this script or at <plugin root>/lib/fcp7xml.py. Reinstall the video-post "
        "plugin, or repackage this skill with tools/build_skills.py." % exc)


def probe_video(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path], text=True)
    data = json.loads(out)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    num, den = v["r_frame_rate"].split("/")
    return {
        "width": int(v["width"]), "height": int(v["height"]),
        "fps": Fraction(int(num), int(den)),
        "duration": float(data["format"]["duration"]),
        "channels": int(a["channels"]) if a else 2,
    }


def probe_image_size(path):
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "default=noprint_wrappers=1", path], text=True)
        vals = dict(l.split("=") for l in out.strip().splitlines() if "=" in l)
        return int(vals["width"]), int(vals["height"])
    except Exception:
        return None, None


def render_overlays(camera, slides, media_dir, info, args):
    """One overlay clip per slide range, rather than one spanning the whole
    video: outside a slide range the cutout is not on screen, so rendering it
    there would be a gigabyte of transparent pixels nobody sees."""
    overlays = []
    for i, slide in enumerate(slides, 1):
        start = max(0.0, slide["start"] - args.handles)
        end = min(info["duration"], slide["end"] + args.handles)
        out_path = os.path.join(media_dir, "overlay_%02d.mov" % i)

        outer = int(round(info["width"] * args.diameter_pct / 100.0))
        if outer % 2:
            outer += 1
        pos_x, pos_y = ovl.corner_xy(args.position, info["width"], info["height"],
                                     outer, args.margin_px)
        vf = ovl.build_filter(info["width"], info["height"], outer, args.border_px,
                              args.border_color, pos_x, pos_y, args.crop_offset_x)

        cmd = (["ffmpeg", "-y", "-ss", "%.3f" % start, "-to", "%.3f" % end,
                "-i", camera, "-an", "-vf", vf]
               + ovl.CODECS[args.codec] + [out_path])
        print("  overlay %02d  %.2fs -> %.2fs" % (i, start, end))
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        if result.returncode != 0:
            sys.stderr.write(result.stderr[-1500:])
            raise SystemExit("Overlay render failed for slide %d." % i)

        overlays.append({"path": out_path, "start": start, "end": end})
    return overlays


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("camera_video")
    ap.add_argument("resolved_slides_json")
    ap.add_argument("output_dir")
    ap.add_argument("--basename", default=None)
    ap.add_argument("--diameter-pct", type=float, default=22.0)
    ap.add_argument("--border-px", type=int, default=6)
    ap.add_argument("--border-color", type=ovl.parse_color, default="#FFFFFF",
                    help="Ring colour as 6-digit hex; set it to the brand colour.")
    ap.add_argument("--position", default="bottom-right",
                    choices=["bottom-right", "bottom-left", "top-right", "top-left"])
    ap.add_argument("--margin-px", type=int, default=48)
    ap.add_argument("--crop-offset-x", type=int, default=0)
    ap.add_argument("--codec", default="prores4444", choices=sorted(ovl.CODECS))
    ap.add_argument("--handles", type=float, default=0.0,
                    help="Extra seconds of overlay rendered either side of each slide range, "
                         "so the cutout can be extended in the editor without re-rendering.")
    args = ap.parse_args()

    with open(args.resolved_slides_json, encoding="utf-8") as f:
        resolved = json.load(f)
    slides = resolved["slides"]
    if not slides:
        raise SystemExit("No resolved slides -- fix the cue phrases before building a timeline.")

    info = probe_video(args.camera_video)
    basename = args.basename or os.path.splitext(os.path.basename(args.camera_video))[0]
    out_dir = os.path.abspath(args.output_dir)
    media_dir = os.path.join(out_dir, "%s_media" % basename)
    os.makedirs(media_dir, exist_ok=True)

    print("Rendering %d cutout overlay(s):" % len(slides))
    overlays = render_overlays(args.camera_video, slides, media_dir, info, args)

    # Copy the slide images in so the timeline does not depend on wherever they
    # were sitting when it was built.
    slide_clips = []
    for i, slide in enumerate(slides, 1):
        src = slide["image"]
        dest = os.path.join(media_dir, "slide_%02d%s" % (i, os.path.splitext(src)[1].lower()))
        shutil.copy2(src, dest)
        w, h = probe_image_size(dest)
        slide_clips.append(Clip(path=dest, start=slide["start"], end=slide["end"],
                                still=True, width=w, height=h,
                                name=os.path.basename(dest)))

    camera_clip = Clip(path=os.path.abspath(args.camera_video), start=0.0,
                       end=info["duration"], source_in=0.0)
    overlay_clips = [Clip(path=o["path"], start=o["start"], end=o["end"],
                          source_in=0.0, alpha=True,
                          name=os.path.basename(o["path"])) for o in overlays]

    xml = build_fcp7_xml(
        sequence_name="%s_slides" % basename,
        fps=info["fps"], width=info["width"], height=info["height"],
        video_tracks=[[camera_clip], slide_clips, overlay_clips],
        audio_tracks=[[camera_clip]],
        audio_channels=info["channels"], source_duration=info["duration"],
    )

    xml_path = os.path.join(out_dir, "%s_slides.xml" % basename)
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml)

    total_overlay_mb = sum(os.path.getsize(o["path"]) for o in overlays) / 1e6
    print("\nDelivered to %s:" % out_dir)
    print("  %s" % os.path.basename(xml_path))
    print("  %s/  (%d overlay(s) + %d slide(s), %.0f MB)"
          % (os.path.basename(media_dir), len(overlays), len(slide_clips), total_overlay_mb))
    print("\nImport the .xml; keep the _media folder and the camera file where they are.")


if __name__ == "__main__":
    main()
