"""
Assemble the slide timeline: render the cutout overlays, gather the media, and
write an FCP7 XML the editor can open and rearrange.

WHAT LANDS ON THE TIMELINE
--------------------------
    V3   circular cutout overlays   (transparent everywhere but the circle)
    V2   slides                     (one clip per slide, at its own range)
    V1   the rough cut's own clips  (individual, still adjustable)
    A1/A2  the camera audio, one track per channel, linked to V1

ONE TIMELINE, NOT TWO
---------------------
V1 comes from the rough cut's XML, not from the flattened trimmed video. That
matters: importing the rough-cut XML and a separate slides XML produced two
unrelated timelines -- one with the adjustable cut, one with a merged copy of it
plus the overlays -- which is useless, because the reason to keep the cut as
clips is to be able to nudge them, and the reason to have overlays is for them
to sit above that. Pass --rough-cut-xml and everything shares one sequence.

Stacked that way, the speaker is full frame wherever no slide covers her, and
during a slide range the slide hides the camera while the cutout sits on top.
Nothing needs an effect applied, and every piece is an ordinary clip. Only the
circle's geometry is fixed, because that cannot travel through this format.

MEDIA LIVES BESIDE THE XML
--------------------------
Slides are encoded as short videos into the media folder rather than referenced
as still images -- a still reference imported as offline media in Resolve while
the movie beside it linked fine, and a static frame encodes to a few hundred KB,
so the trade is nearly free. An interchange XML holds absolute paths, so keeping
the media next to the XML makes the handoff self-contained -- except the camera
file itself, far too large to duplicate, which has to stay put.

Usage:
    python build_timeline.py <camera_video> <resolved_slides.json> <output_dir>
        [--basename NAME]
        [--diameter-pct 22] [--border-px 6] [--border-color "#1E90FF"]
        [--position bottom-right] [--margin-px 48] [--crop-offset-x 0]
        [--codec prores4444] [--handles 0.0]
        [--rough-cut-xml lesson.xml]   put the slides on the SAME timeline as the cut
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse

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


def _path_from_pathurl(pathurl):
    p = unquote(urlparse(pathurl).path)
    if len(p) > 2 and p[0] == "/" and p[2] == ":":
        p = p[1:]
    return p


def camera_clips_from_rough_cut(xml_path):
    """Read V1 out of a rough-cut timeline so the slides can sit on top of the
    actual cut rather than on top of a flattened copy of it.

    Importing two XMLs gave two separate timelines -- one with the adjustable
    cut, one with the merged video plus overlays -- which is not usable: the
    whole point of the cut timeline is that its clips can still be nudged, and
    the whole point of the overlays is that they sit above that. Reading the
    rough cut's clips here puts everything in one sequence.
    """
    root = ET.parse(xml_path).getroot()
    seq = root.find("sequence")
    timebase = int(seq.findtext("rate/timebase"))
    files = {f.get("id"): _path_from_pathurl(f.findtext("pathurl"))
             for f in seq.findall(".//file") if f.findtext("pathurl")}

    track = seq.find("./media/video/track")
    if track is None:
        raise SystemExit("No video track found in %s" % xml_path)

    clips = []
    for c in track.findall("clipitem"):
        ref = c.find("file")
        path = files.get(ref.get("id")) if ref is not None else None
        if not path:
            raise SystemExit("A clip in %s references an unknown file id" % xml_path)
        clips.append(Clip(
            path=path,
            start=int(c.findtext("start")) / timebase,
            end=int(c.findtext("end")) / timebase,
            source_in=int(c.findtext("in")) / timebase,
            name=c.findtext("name"),
        ))
    if not clips:
        raise SystemExit("No clips found on V1 of %s" % xml_path)
    return clips


def render_slide_videos(slides, media_dir, info):
    """Encode each slide as a short video rather than referencing the PNG.

    A still-image reference is the one thing in this format an importer handled
    badly in practice -- the PNG came up as offline media in Resolve while the
    movie beside it linked fine. A static frame encodes to a few hundred KB for
    a couple of minutes, so trading the still for a movie costs almost nothing
    and removes a whole class of importer quirk.
    """
    out = []
    for i, slide in enumerate(slides, 1):
        duration = slide["end"] - slide["start"]
        dest = os.path.join(media_dir, "slide_%02d.mp4" % i)
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", slide["image"],
               "-t", "%.3f" % duration, "-r", str(int(round(float(info["fps"])))),
               "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
               "-vf", "scale=%d:%d" % (info["width"], info["height"]), dest]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        if result.returncode != 0:
            sys.stderr.write(result.stderr[-1500:])
            raise SystemExit("Could not encode slide %d." % i)
        out.append(dest)
    return out


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
        "sample_rate": int(a.get("sample_rate", 48000)) if a else 48000,
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

        # One geometry helper for both scripts, so the cutout cannot come out one
        # size here and another there.
        margin_pct = args.margin_pct
        if args.margin_px is not None:
            margin_pct = 100.0 * args.margin_px / info["width"]
        outer, _margin, pos_x, pos_y, _clamped = ovl.resolve_geometry(
            info["width"], info["height"], args.diameter_pct, margin_pct, args.position)
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

        box = ovl.measure_opaque_box(out_path)
        if box is None:
            raise SystemExit("Overlay %d has no opaque pixels -- the mask did not work." % i)
        x0, y0, x1, y1 = box
        got = max(x1 - x0, y1 - y0)
        tol = max(0.02 * info["width"], 24)
        if abs(got - outer) > tol or abs(x0 - pos_x) > tol or abs(y0 - pos_y) > tol:
            raise SystemExit(
                "Overlay %d geometry is wrong: %dpx circle at (%d,%d), expected %dpx at "
                "(%d,%d). Refusing to hand over a cutout that does not match the requested "
                "look." % (i, got, x0, y0, outer, pos_x, pos_y))

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
    ap.add_argument("--margin-pct", type=float, default=2.5,
                    help="Gap from the frame edge as a percentage of frame width, so the look "
                         "is the same at 1080p and 4K.")
    ap.add_argument("--margin-px", type=int, default=None,
                    help="Absolute-pixel override for the margin. Prefer --margin-pct.")
    ap.add_argument("--crop-offset-x", type=int, default=0)
    ap.add_argument("--codec", default="prores4444", choices=sorted(ovl.CODECS))
    ap.add_argument("--rough-cut-xml", default=None,
                    help="The rough cut's .xml. Its clips become V1, so the slides and "
                         "cutouts land in the SAME timeline as the adjustable cut instead of "
                         "a second one. Strongly recommended: without it V1 is the flattened "
                         "trimmed video and the cut can no longer be altered.")
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

    # Slides become short videos rather than still references -- see
    # render_slide_videos for why.
    print("Encoding %d slide(s) as video:" % len(slides))
    slide_paths = render_slide_videos(slides, media_dir, info)
    slide_clips = [Clip(path=p, start=s["start"], end=s["end"], source_in=0.0,
                        has_audio=False, name=os.path.basename(p))
                   for p, s in zip(slide_paths, slides)]

    # V1: the rough cut's own clips when available, so everything shares one
    # timeline and the cut stays adjustable.
    if args.rough_cut_xml:
        camera_clips = camera_clips_from_rough_cut(args.rough_cut_xml)
        print("V1 from rough cut: %d clip(s), still individually adjustable"
              % len(camera_clips))
    else:
        camera_clips = [Clip(path=os.path.abspath(args.camera_video), start=0.0,
                             end=info["duration"], source_in=0.0)]
        print("V1 is the flattened trimmed video (pass --rough-cut-xml to keep "
              "the cut adjustable)")

    overlay_clips = [Clip(path=o["path"], start=o["start"], end=o["end"],
                          source_in=0.0, alpha=True, has_audio=False,
                          name=os.path.basename(o["path"])) for o in overlays]

    # Audio comes from the trimmed cut rather than the original footage. Pointing
    # picture and sound at one file makes the importer invent an audio-only view
    # of it, and that phantom is what imported as offline media. Two distinct
    # real files give it nothing to guess.
    # The rough cut already wrote the cut audio as its own file next to the
    # timeline. Reuse it: it is inside the output folder, which is where the
    # editor searches, and it keeps video and audio pointing at different files.
    audio_source = os.path.join(out_dir, "%s_audio.wav" % basename)
    if not os.path.isfile(audio_source):
        audio_source = os.path.abspath(args.camera_video)
        print("note: %s_audio.wav not found; falling back to the trimmed video for audio"
              % basename)
    audio_clips = [Clip(path=audio_source, start=c.start, end=c.end, source_in=c.start)
                   for c in camera_clips]

    xml = build_fcp7_xml(
        sequence_name="%s_slides" % basename,
        fps=info["fps"], width=info["width"], height=info["height"],
        video_tracks=[camera_clips, slide_clips, overlay_clips],
        audio_clips=audio_clips,
        audio_channels=info["channels"], audio_sample_rate=info["sample_rate"],
        source_duration=info["duration"],
    )

    # One timeline, one file. Emitting a second XML beside the rough cut's meant
    # the user imported both and got two sequences -- documenting "import only
    # this one" did not stop that, because two files in a folder invite two
    # imports. The merged timeline supersedes the rough cut's, so it takes over
    # the same filename and the old one is removed.
    xml_path = os.path.join(out_dir, "%s.xml" % basename)
    superseded = os.path.abspath(args.rough_cut_xml) if args.rough_cut_xml else None
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml)

    replaces_input = (superseded and os.path.isfile(superseded)
                      and os.path.abspath(xml_path) != superseded)
    if replaces_input:
        os.remove(superseded)
        print("removed the superseded rough-cut timeline: %s"
              % os.path.basename(superseded))

    total_overlay_mb = sum(os.path.getsize(o["path"]) for o in overlays) / 1e6
    print("\nDelivered to %s:" % out_dir)
    print("  %s" % os.path.basename(xml_path))
    print("  %s/  (%d overlay(s) + %d slide video(s), %.0f MB)"
          % (os.path.basename(media_dir), len(overlays), len(slide_clips), total_overlay_mb))
    print("\nImport the .xml; keep every other file in the folder where it is --")
    print("editors find media by searching that folder, not by the paths in the XML.")


if __name__ == "__main__":
    main()
