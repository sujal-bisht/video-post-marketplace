"""Bake the finished edit into one video someone can post, straight from the timeline.

WHY THIS EXISTS
---------------
Everything else in the plugin builds an editable timeline. That is ideal for
anyone who owns Premiere or Resolve and useless to anyone who does not: CapCut
cannot import an XML, and neither can Instagram. Until this step, the zooms only
existed as keyframes inside that XML, so no video file anywhere actually
contained them.

This reads the finished timeline and reproduces it in one ffmpeg pass: the cut
video, the zooms, any slides and cutout, and the captions on top. The timeline
stays the single source of truth; this is a rendering of it, not a second
version of the edit that could drift away from it.

The captions are also rendered once more as a transparent layer and added to
the timeline on a track of their own, so editor users can move or switch them
off like any other clip.

WINDOWS PATHS AND FFMPEG FILTERS
--------------------------------
ffmpeg's filter syntax treats a colon as an option separator, so a path like
C:\\Users\\... inside a filter breaks the whole command. Every file a filter needs
-- the caption script and its fonts -- is copied into a scratch folder and the
command runs from there with relative names. Input files passed with -i are not
part of a filter and keep their full paths.
"""
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse

import zoom as zoom_lib


def _path_from_url(url):
    p = unquote(urlparse(url).path)
    if p.startswith("/") and len(p) > 2 and p[2] == ":":
        p = p[1:]
    return os.path.normpath(p)


def timeline_shape(xml_path):
    root = ET.parse(xml_path).getroot()
    seq = root.find("sequence")
    fps = float(seq.findtext("rate/timebase") or 25)
    if (seq.findtext("rate/ntsc") or "FALSE").upper() == "TRUE":
        fps = fps * 1000.0 / 1001.0
    w = int(seq.findtext("media/video/format/samplecharacteristics/width") or 0)
    h = int(seq.findtext("media/video/format/samplecharacteristics/height") or 0)
    dur = int(seq.findtext("duration") or 0) / fps
    return fps, w, h, dur


def overlays_from_xml(xml_path):
    """Every clip above the camera track, as it should appear in the render."""
    root = ET.parse(xml_path).getroot()
    seq = root.find("sequence")
    fps, _, _, _ = timeline_shape(xml_path)
    files = {f.get("id"): _path_from_url(f.findtext("pathurl"))
             for f in root.iter("file") if f.findtext("pathurl")}
    out = []
    tracks = seq.findall("media/video/track")
    for ti, track in enumerate(tracks[1:], start=2):
        for ci in track.findall("clipitem"):
            fe = ci.find("file")
            if fe is None or fe.get("id") not in files:
                continue
            path = files[fe.get("id")]
            # the caption layer is burned separately, never composited twice
            if os.path.basename(path).endswith("_captions.mov"):
                continue
            out.append({"track": ti, "path": path,
                        "start": int(ci.findtext("start")) / fps,
                        "end": int(ci.findtext("end")) / fps,
                        "in": int(ci.findtext("in")) / fps})
    out.sort(key=lambda o: (o["track"], o["start"]))
    return out


def zoom_curve(xml_path):
    """The zoom as (time, percent) points, and the spans where it is above 100%.

    Rebuilt from the keyframes in the finished timeline rather than from the
    zoom plan, so the render cannot disagree with the timeline an editor opens.
    """
    points, fps, _ = zoom_lib.read_ramp_from_xml(xml_path)
    merged = {}
    for t, v in points:
        merged[round(t, 3)] = v
    pts = sorted(merged.items())
    if not pts or all(abs(v - 100.0) < 0.01 for _, v in pts):
        return [], []
    spans, start = [], None
    for (t1, v1), (t2, v2) in zip(pts, pts[1:]):
        moving = v1 > 100.01 or v2 > 100.01
        if moving and start is None:
            start = t1
        if not moving and start is not None:
            spans.append((start, t1))
            start = None
    if start is not None:
        spans.append((start, pts[-1][0]))
    return pts, spans


def zoom_filter(xml_path, fps):
    """An ffmpeg filter reproducing the timeline's zoom, or None.

    WHY PERSPECTIVE, NOT SCALE OR ZOOMPAN
    The obvious version -- scale up by an expression of time, crop back -- does
    nothing at all: ffmpeg fixes a filter's output size when the graph starts,
    so the expression was read once, at 100%, and every frame came out unzoomed.
    The render measured flat 100% where the timeline said 109%.
    zoompan does animate, but it positions the crop on whole pixels, which makes
    a slow push-in visibly shudder. perspective re-maps the four corners of each
    frame with sub-pixel interpolation into a fixed-size output: smooth, and the
    size never changes, so nothing has to re-configure.

    It only runs while a zoom is on screen. Outside the ramps it would be an
    identity transform costing the same as a real one; measured, restricting it
    cut the added render time by more than half.
    """
    pts, spans = zoom_curve(xml_path)
    if not spans:
        return None
    t = "(in/%.6f)" % fps          # perspective counts frames; it has no clock
    expr = "100"
    for (t1, v1), (t2, v2) in reversed(list(zip(pts, pts[1:]))):
        if t2 <= t1:
            continue
        seg = "%.4f+(%.4f)*(%s-%.4f)/%.4f" % (v1, v2 - v1, t, t1, t2 - t1)
        expr = "if(between(%s,%.4f,%.4f),%s,%s)" % (t, t1, t2, seg, expr)
    inset = "(1-100/(%s))/2" % expr
    corners = {"x0": "W*%s" % inset, "y0": "H*%s" % inset,
               "x1": "W-W*%s" % inset, "y1": "H*%s" % inset,
               "x2": "W*%s" % inset, "y2": "H-H*%s" % inset,
               "x3": "W-W*%s" % inset, "y3": "H-H*%s" % inset}
    enable = "+".join("between(t,%.3f,%.3f)" % s for s in spans)
    # Linear, not cubic: at a 10% push the two are indistinguishable in motion,
    # and linear costs noticeably less on every zoomed frame.
    return ("perspective=" + ":".join("%s='%s'" % kv for kv in corners.items())
            + ":interpolation=linear:eval=frame:enable='%s'" % enable)


# Short-form platforms -- Instagram, TikTok, LinkedIn, YouTube Shorts -- cap
# uploads at 1080 pixels on the short side and re-compress anything larger.
# Rendering a 4K phone clip at 4K for them is work that is thrown away on upload,
# at about four times the cost. The editable timeline keeps full resolution.
POST_SHORT_SIDE = 1080


def output_size(w, h, full_resolution=False):
    if full_resolution or min(w, h) <= POST_SHORT_SIDE:
        return w, h
    s = POST_SHORT_SIDE / float(min(w, h))
    return int(round(w * s / 2)) * 2, int(round(h * s / 2)) * 2


def _run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        tail = "\n".join(r.stderr.strip().splitlines()[-6:])
        raise SystemExit("ffmpeg failed:\n%s" % tail)
    return r.stderr


def _stage(ass_path, fonts):
    """Scratch folder holding the caption script and its fonts under short names."""
    work = tempfile.mkdtemp(prefix="vp-render-")
    if ass_path:
        shutil.copy(ass_path, os.path.join(work, "captions.ass"))
    os.makedirs(os.path.join(work, "fonts"), exist_ok=True)
    if fonts is not None:
        for p in fonts.files.values():
            shutil.copy(p, os.path.join(work, "fonts"))
    return work


def render_final(base_video, xml_path, out_path, ass_path=None, fonts=None,
                 crf=20, preset="veryfast", full_resolution=False):
    """One pass: cut video, zooms, overlays, captions. Returns the ffmpeg log.

    Scaled down FIRST when the source is bigger than the post size, so the zoom,
    the overlays and the captions all work on the smaller frame rather than
    being done at 4K and thrown away. The caption script is written in the
    timeline's own coordinates and libass rescales it to whatever it draws on,
    so the same captions come out in the same place at any size.
    """
    fps, w, h, dur = timeline_shape(xml_path)
    ow, oh = output_size(w, h, full_resolution)
    work = _stage(ass_path, fonts)
    try:
        cmd = ["ffmpeg", "-y", "-v", "verbose", "-i", os.path.abspath(base_video)]
        overlays = overlays_from_xml(xml_path)
        for o in overlays:
            cmd += ["-i", os.path.abspath(o["path"])]

        chain = []
        base = "[0:v]"
        if (ow, oh) != (w, h):
            chain.append("[0:v]scale=%d:%d:flags=lanczos[vs]" % (ow, oh))
            base = "[vs]"
        z = zoom_filter(xml_path, fps)
        chain.append("%s%s[v0]" % (base, z or "null"))

        last = "v0"
        for i, o in enumerate(overlays, start=1):
            length = o["end"] - o["start"]
            chain.append("[%d:v]trim=start=%.4f:duration=%.4f,setpts=PTS-STARTPTS+%.4f/TB,"
                         "scale=%d:%d[o%d]" % (i, o["in"], length, o["start"], ow, oh, i))
            chain.append("[%s][o%d]overlay=eof_action=pass:enable='between(t,%.4f,%.4f)'[v%d]"
                         % (last, i, o["start"], o["end"], i))
            last = "v%d" % i

        if ass_path:
            chain.append("[%s]subtitles=captions.ass:fontsdir=fonts[vout]" % last)
        else:
            chain.append("[%s]null[vout]" % last)

        cmd += ["-filter_complex", ";".join(chain), "-map", "[vout]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                os.path.abspath(out_path)]
        return _run(cmd, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def burn_captions(video, ass_path, fonts, out_path, full_resolution=False, crf=20,
                  preset="veryfast"):
    """Captions onto a video that is already edited. No timeline involved."""
    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                           "-show_entries", "stream=width,height", "-of", "csv=p=0",
                           os.path.abspath(video)], capture_output=True, text=True).stdout
    w, h = [int(x) for x in info.strip().split(",")[:2]]
    ow, oh = output_size(w, h, full_resolution)
    work = _stage(ass_path, fonts)
    try:
        vf = ("scale=%d:%d:flags=lanczos," % (ow, oh) if (ow, oh) != (w, h) else "")
        vf += "subtitles=captions.ass:fontsdir=fonts"
        cmd = ["ffmpeg", "-y", "-v", "verbose", "-i", os.path.abspath(video), "-vf", vf,
               "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", preset,
               "-crf", str(crf), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
               "-movflags", "+faststart", os.path.abspath(out_path)]
        return _run(cmd, work), (ow, oh)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def render_caption_layer(ass_path, fonts, out_mov, width, height, fps, duration):
    """The captions alone on a transparent canvas, for the timeline's caption track.

    qtrle rather than ProRes 4444: a caption frame is almost entirely empty, and
    run-length coding stores empty pixels for next to nothing.
    """
    work = _stage(ass_path, fonts)
    try:
        cmd = ["ffmpeg", "-y", "-v", "verbose", "-f", "lavfi",
               "-i", "color=c=black@0.0:s=%dx%d:r=%s:d=%.3f,format=rgba"
               % (width, height, _rate(fps), duration),
               "-vf", "subtitles=captions.ass:fontsdir=fonts:alpha=1",
               "-c:v", "qtrle", "-pix_fmt", "argb", os.path.abspath(out_mov)]
        return _run(cmd, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _rate(fps):
    for num, den in ((24000, 1001), (30000, 1001), (60000, 1001)):
        if abs(fps - num / float(den)) < 0.01:
            return "%d/%d" % (num, den)
    return "%g" % fps


def add_caption_track(xml_path, mov_path, width, height):
    """Put the caption layer on its own top track of the existing timeline.

    Edits the file in place, like every other step: one timeline per folder.
    Re-running replaces the previous caption track rather than adding another.
    """
    fps, _, _, dur = timeline_shape(xml_path)
    text = open(xml_path, encoding="utf-8").read()
    text = re.sub(r"\s*<track><!-- vp-captions -->.*?</track>", "", text, flags=re.S)

    frames = int(round(dur * fps))
    tb = int(round(fps))
    ntsc = "TRUE" if abs(fps - round(fps)) > 0.01 else "FALSE"
    rate = "<rate><timebase>%d</timebase><ntsc>%s</ntsc></rate>" % (tb, ntsc)
    from fcp7xml import _pathurl
    name = os.path.basename(mov_path)
    track = (
        "<track><!-- vp-captions -->"
        '<clipitem id="captions-1"><name>%s</name>%s'
        "<start>0</start><end>%d</end><in>0</in><out>%d</out><enabled>TRUE</enabled>"
        '<file id="file-captions"><name>%s</name><pathurl>%s</pathurl>%s'
        "<duration>%d</duration><media><video><samplecharacteristics>%s"
        "<width>%d</width><height>%d</height><pixelaspectratio>square</pixelaspectratio>"
        "</samplecharacteristics><alphatype>straight</alphatype></video></media></file>"
        "</clipitem></track>"
    ) % (name, rate, frames, frames, name, _pathurl(mov_path), rate, frames, rate, width, height)

    close = _sequence_video_close(text)
    text = text[:close] + track + text[close:]
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(text)


def _sequence_video_close(text):
    """Offset of the </video> that closes the SEQUENCE's video section.

    Not simply the first </video> after <media>: every clip in this format
    carries its own <file><media><video>...</video></media></file>, nested
    inside the sequence's video section. Taking the first closing tag once put
    the caption track inside the first clip's file description, which would
    have corrupted that clip's media on import. So walk the tags and count depth.
    """
    seq = text.index("<sequence")
    media = text.index("<media>", seq)
    start = text.index("<video>", media)
    depth, i = 0, start
    for m in re.finditer(r"<(/?)video>", text[start:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return start + m.start()
    raise SystemExit("Could not find the end of the timeline's video section.")
