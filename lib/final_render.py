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
            # the caption and hook layers are burned from their scripts, never
            # composited a second time from these transparent copies
            if os.path.basename(path).endswith(("_captions.mov", "_hook.mov")):
                continue
            out.append({"track": ti, "path": path,
                        "start": int(ci.findtext("start")) / fps,
                        "end": int(ci.findtext("end")) / fps,
                        "in": int(ci.findtext("in")) / fps})
    out.sort(key=lambda o: (o["track"], o["start"]))
    return out


def picture_source(xml_path):
    """What V1 plays: one media file, and the timeline's pieces of it.

    Returns (media path, [(timeline start, timeline end, source in)], fps) in
    seconds. With a trimmed file the pieces simply follow on from each other;
    with the original footage each piece reads from where it sat in the raw
    recording.
    """
    import sync_check
    clips, fps = sync_check.read_video_clips(xml_path, 1)
    if not clips:
        raise SystemExit("The timeline has no clips on V1.")
    media = {c["media"] for c in clips}
    if len(media) != 1:
        raise SystemExit("V1 plays more than one file (%s); the final render expects one camera."
                         % ", ".join(sorted(os.path.basename(m) for m in media)))
    segs = [(c["start"] / fps, c["end"] / fps, c["in"] / fps) for c in clips]
    return media.pop(), sorted(segs), fps


def audio_source(xml_path):
    """The file the timeline's audio plays (the cut wav), or None."""
    root = ET.parse(xml_path).getroot()
    files = {f.get("id"): _path_from_url(f.findtext("pathurl"))
             for f in root.iter("file") if f.findtext("pathurl")}
    seq = root.find("sequence")
    for track in seq.findall("media/audio/track"):
        for ci in track.findall("clipitem"):
            fe = ci.find("file")
            if fe is not None and fe.get("id") in files and os.path.isfile(files[fe.get("id")]):
                return files[fe.get("id")]
    return None


def timeline_to_source(segs, t):
    """Where timeline time t reads from in the V1 media."""
    for start, end, src_in in segs:
        if start <= t < end:
            return src_in + (t - start)
    start, end, src_in = segs[-1]
    return src_in + (min(t, end) - start)


def cut_graph(segs, fps, src="0:v", out="cut"):
    """Filter-graph lines that play only the timeline's pieces of the source.

    None when V1 already plays its media straight through (a trimmed file).

    One trim per piece, then one concat. A single select() listing every piece
    was tried first and is shorter to write, but a 6-minute phone video has
    300+ pieces and ffmpeg ran out of memory just parsing that expression. Many
    small filters are fine: this is how the rough cut has always rendered, and
    the decoder still runs once, feeding every trim.

    Frame-exact: each piece keeps the frames from in - half a frame up to
    out - half a frame, which is exactly the frames the timeline's integer
    frame counts describe, whatever rounding the seconds picked up on the way.
    """
    half = 0.5 / fps
    straight = all(abs(s - i) < half for s, e, i in segs) and all(
        abs(segs[k][0] - segs[k - 1][1]) < half for k in range(1, len(segs)))
    if straight:
        return None
    lines = ["[%s]split=%d%s" % (src, len(segs), "".join("[p%d]" % k for k in range(len(segs))))]
    for k, (s, e, i) in enumerate(segs):
        lines.append("[p%d]trim=start=%.6f:end=%.6f,setpts=PTS-STARTPTS[q%d]"
                     % (k, max(0.0, i - half), i + (e - s) - half, k))
    lines.append("%sconcat=n=%d:v=1:a=0[%s]"
                 % ("".join("[q%d]" % k for k in range(len(segs))), len(segs), out))
    return lines


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
    # ONE FILTER PER ZOOM, not one for the whole video. A single expression
    # covering every keyframe broke twice on a 4-minute video with 13 zooms:
    # written as nested if()s it was 175 deep and ffmpeg refused it ("Invalid
    # argument"); written as a flat sum it was 130,000 characters and ffmpeg
    # ran out of memory evaluating it. Each zoom on its own is a dozen
    # keyframes, switched on only for its own few seconds, and a filter that is
    # switched off passes frames through for free.
    parts = []
    for s0, s1 in spans:
        terms = []
        for (t1, v1), (t2, v2) in zip(pts, pts[1:]):
            if t2 <= t1 or t2 < s0 - 1e-6 or t1 > s1 + 1e-6:
                continue
            if abs(v1 - 100.0) < 0.001 and abs(v2 - 100.0) < 0.001:
                continue
            terms.append("gte(%s,%.4f)*lt(%s,%.4f)*(%.4f+(%.4f)*(%s-%.4f)/%.4f)"
                         % (t, t1, t, t2, v1 - 100.0, v2 - v1, t, t1, t2 - t1))
        if not terms:
            continue
        inset = "(1-100/(100+%s))/2" % "+".join(terms)
        corners = (("x0", "W*%s" % inset), ("y0", "H*%s" % inset),
                   ("x1", "W-W*%s" % inset), ("y1", "H*%s" % inset),
                   ("x2", "W*%s" % inset), ("y2", "H-H*%s" % inset),
                   ("x3", "W-W*%s" % inset), ("y3", "H-H*%s" % inset))
        # Linear, not cubic: at a 10% push the two are indistinguishable in
        # motion, and linear costs noticeably less on every zoomed frame.
        parts.append("perspective=" + ":".join("%s='%s'" % kv for kv in corners)
                     + ":interpolation=linear:eval=frame:enable='between(t,%.3f,%.3f)'"
                     % (s0, s1))
    return ",".join(parts) or None


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
    """One pass: cut, zooms, overlays, captions. Returns the ffmpeg log.

    Reads the picture from whatever V1 plays -- the original footage, cut on the
    fly, or a trimmed file -- and the sound from the timeline's own audio (the
    cut wav). `base_video` is only a fallback for timelines with no audio track.

    Scaled down FIRST, right after the cut, so the zoom, the overlays and the
    captions all work on the post-size frame instead of on 4K. The caption
    script is written in the timeline's own coordinates and libass rescales it
    to whatever it draws on, so captions land in the same place at any size.

    The whole graph goes to ffmpeg as a file, not on the command line: with a
    few hundred cuts it is far longer than Windows lets a command be.
    """
    fps, w, h, dur = timeline_shape(xml_path)
    ow, oh = output_size(w, h, full_resolution)
    media = picture_source(xml_path)[0]
    import hwdecode
    hw_args, head = hwdecode.choose(os.path.abspath(media), ow, oh)
    try:
        return _render_final(base_video, xml_path, out_path, ass_path, fonts, crf, preset,
                             full_resolution, hw_args, head)
    except SystemExit:
        if not hw_args:
            raise
        # The GPU path is only ever an optimisation. If it fails on this
        # footage, the software path renders the same thing, just slower.
        print("GPU decoding failed on this video; rendering in software instead.")
        return _render_final(base_video, xml_path, out_path, ass_path, fonts, crf, preset,
                             full_resolution, [], None)


def _render_final(base_video, xml_path, out_path, ass_path, fonts, crf, preset,
                  full_resolution, hw_args, head):
    fps, w, h, dur = timeline_shape(xml_path)
    ow, oh = output_size(w, h, full_resolution)
    if head is None:
        head = "scale=%d:%d:flags=lanczos" % (ow, oh) if (ow, oh) != (w, h) else "null"
    media, segs, _ = picture_source(xml_path)
    sound = audio_source(xml_path)
    work = _stage(ass_path, fonts)
    try:
        cmd = ["ffmpeg", "-y", "-v", "verbose"] + list(hw_args) + ["-i", os.path.abspath(media)]
        if sound:
            cmd += ["-i", os.path.abspath(sound)]
            audio_map = "1:a"
        else:
            cmd += ["-i", os.path.abspath(base_video)]
            audio_map = "1:a?"
        overlays = overlays_from_xml(xml_path)
        for o in overlays:
            cmd += ["-i", os.path.abspath(o["path"])]

        # CUT FIRST, THEN SCALE -- on the GPU too. The cut has to read the
        # camera's own timestamps, and the GPU scaler does not keep them: it
        # re-stamps every frame on a perfectly regular clock, and phone footage
        # is not perfectly regular. Scaled first, the cut drifted a frame off
        # the timeline (found frame by frame against the old render: 2 frames
        # short in 20 pieces). Cut first, it matched every frame. Cutting first
        # also means the footage that was cut out is never scaled at all.
        chain, pre = [], []
        src = "0:v"
        if head != "null":
            pre.append(head)
        cut = cut_graph(segs, fps, src=src)
        if cut:
            chain.extend(cut)
            src = "cut"
        z = zoom_filter(xml_path, fps)
        if z:
            pre.append(z)
        chain.append("[%s]%s[v0]" % (src, ",".join(pre) or "null"))

        last = "v0"
        for i, o in enumerate(overlays, start=2):
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

        with open(os.path.join(work, "graph.txt"), "w", encoding="utf-8") as f:
            f.write(";\n".join(chain))
        cmd += ["-filter_complex_script", "graph.txt", "-map", "[vout]", "-map", audio_map,
                "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                "-t", "%.3f" % dur, os.path.abspath(out_path)]
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
    """Put the caption layer on its own top track of the existing timeline."""
    add_layer_track(xml_path, mov_path, width, height, "captions")


def add_layer_track(xml_path, mov_path, width, height, kind, start=0.0, end=None):
    """Put a transparent layer (captions, hook) on its own top track.

    Edits the file in place, like every other step: one timeline per folder.
    Re-running replaces the previous track of the same kind rather than adding
    another. `start`/`end` are seconds on the timeline; the layer file itself
    starts at its own frame 0.
    """
    fps, _, _, dur = timeline_shape(xml_path)
    text = open(xml_path, encoding="utf-8").read()
    marker = "<!-- vp-%s -->" % kind
    text = re.sub(r"\s*<track>%s.*?</track>" % re.escape(marker), "", text, flags=re.S)

    first = int(round(start * fps))
    last = int(round((dur if end is None else min(end, dur)) * fps))
    length = last - first
    tb = int(round(fps))
    ntsc = "TRUE" if abs(fps - round(fps)) > 0.01 else "FALSE"
    rate = "<rate><timebase>%d</timebase><ntsc>%s</ntsc></rate>" % (tb, ntsc)
    from fcp7xml import _pathurl
    name = os.path.basename(mov_path)
    track = (
        "<track>%s"
        '<clipitem id="%s-1"><name>%s</name>%s'
        "<start>%d</start><end>%d</end><in>0</in><out>%d</out><enabled>TRUE</enabled>"
        '<file id="file-%s"><name>%s</name><pathurl>%s</pathurl>%s'
        "<duration>%d</duration><media><video><samplecharacteristics>%s"
        "<width>%d</width><height>%d</height><pixelaspectratio>square</pixelaspectratio>"
        "</samplecharacteristics><alphatype>straight</alphatype></video></media></file>"
        "</clipitem></track>"
    ) % (marker, kind, name, rate, first, last, length, kind, name, _pathurl(mov_path), rate,
         length, rate, width, height)

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
