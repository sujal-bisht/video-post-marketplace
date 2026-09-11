"""Slow zooms (push-ins) written into an existing FCP7 XML timeline.

WHY IT WORKS THIS WAY
---------------------
An adjustment layer would be the obvious tool -- one clip above the cut, one
ramp, every clip underneath affected. It cannot be shipped. Resolve was asked to
build one and export it: the adjustment clip came back out as a "Solid Color"
generator, and re-importing that file put an opaque colour clip on V1. An
adjustment layer only exists inside a project, never in the interchange file.

So each clip carries its own slice of the ramp. That sounds fragile and is not:
the ramp is one straight line in TIMELINE time, and every clip reads its own
start and end values off that same line, so the slices meet exactly by
arithmetic rather than by hand-alignment.

THE RULE THAT COST A RENDER TO FIND
-----------------------------------
Keyframe <when> values are absolute sequence frames, NOT frames from the start
of the clip that carries them. Written clip-relative, Resolve rendered a lurch
to 106% followed by a near-flat 109% instead of a smooth climb -- measured, not
guessed. Written absolute, a render measured 100.5 / 101.5 / 103.5 / 105.0 /
106.5% against a requested 100.4 / 101.7 / 103.3 / 105.0 / 106.2%: correct
within the half-percent resolution of the measurement.

WHY THE ZOOM COMES BACK OUT
---------------------------
In ordinary editing a zoom can end on a cut, because a cut is a visual break
that hides the change of framing. The cuts here are not: the rough cut removes
silence from one continuous shot, so an edit point is invisible and dropping
from 108% to 100% across it reads as a glitch. The zoom therefore eases back to
100% before it ends. The exception is a junction where a lot of material was
removed -- that IS a real discontinuity, and framing can reset there for free.
"""
import re
import xml.etree.ElementTree as ET

# The look, decided once (see SKILL.md): every zoom is the same 110%. Varying
# the strength was considered and rejected -- zooms that differ slightly read as
# inconsistency rather than as variety. On a 4K sequence this is an upscale of
# the source, which is why it stops at 110 rather than going further.
DEFAULT_PEAK_PCT = 110.0
DEFAULT_RAMP_OUT_S = 2.0
MAX_PEAK_PCT = 120.0

# Marker so a rerun replaces its own work instead of stacking a second zoom on
# top of the first.
MARKER = "<!-- vp-zoom -->"


# ---------------------------------------------------------------- ramp shape

class Ramp(object):
    """One zoom gesture: rise from 100% to a peak, then ease back to 100%.

    Evaluated in timeline seconds. Outside the span it is exactly 100, so clips
    that only touch the edge of a zoom stay untouched.
    """

    def __init__(self, start_s, end_s, peak_pct=DEFAULT_PEAK_PCT,
                 ramp_out_s=DEFAULT_RAMP_OUT_S, hard_out=False):
        if end_s <= start_s:
            raise ValueError("a zoom needs a positive length")
        self.start = float(start_s)
        self.end = float(end_s)
        self.peak = float(peak_pct)
        # A gesture shorter than twice the ease-out has no room to climb, so the
        # ease shrinks with it rather than eating the whole zoom.
        self.ramp_out = 0.0 if hard_out else min(float(ramp_out_s),
                                                 (self.end - self.start) / 3.0)
        self.peak_at = self.end - self.ramp_out

    def at(self, t):
        if t <= self.start or t >= self.end:
            return 100.0
        if t <= self.peak_at:
            span = self.peak_at - self.start
            if span <= 0:
                return self.peak
            return 100.0 + (self.peak - 100.0) * (t - self.start) / span
        return self.peak - (self.peak - 100.0) * (t - self.peak_at) / self.ramp_out

    def breakpoints(self):
        """Times where the curve changes direction and so needs a keyframe."""
        pts = [self.start, self.peak_at, self.end]
        return sorted(set(round(p, 6) for p in pts if p >= 0))

    def covers(self, a, b):
        return b > self.start and a < self.end


# ---------------------------------------------------------- reading a timeline

def _fps_of(seq):
    fps = float(seq.findtext("rate/timebase") or 25)
    if (seq.findtext("rate/ntsc") or "FALSE").upper() == "TRUE":
        fps = fps * 1000.0 / 1001.0
    return fps


def read_timeline(xml_path):
    """Structure needed to place zooms: fps, duration, V1 clips, covered spans.

    `covered` are the spans where a slide sits over the camera. Zooming the
    camera track there does nothing a viewer can see -- she is a small circle in
    the corner -- so those spans are not eligible.
    """
    root = ET.parse(xml_path).getroot()
    seq = root.find("sequence")
    fps = _fps_of(seq)
    tracks = seq.findall("media/video/track")

    v1 = []
    for ci in (tracks[0].findall("clipitem") if tracks else []):
        v1.append({"start": int(ci.findtext("start")) / fps,
                   "end": int(ci.findtext("end")) / fps,
                   "in": int(ci.findtext("in")) / fps,
                   "name": ci.findtext("name") or ""})
    v1.sort(key=lambda c: c["start"])

    covered = []
    for track in tracks[1:]:
        for ci in track.findall("clipitem"):
            covered.append((int(ci.findtext("start")) / fps,
                            int(ci.findtext("end")) / fps))
    covered = _merge(covered)

    duration = float(seq.findtext("duration") or 0) / fps
    if v1:
        duration = max(duration, v1[-1]["end"])
    return {"fps": fps, "duration": duration, "v1": v1, "covered": covered}


def _merge(spans):
    out = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def real_discontinuities(v1, min_removed_s=1.0):
    """Junctions where enough was cut that the picture genuinely jumps.

    Only knowable when the clips reference the ORIGINAL footage, where the gap
    between one clip's out-point and the next one's in-point is the material
    that was removed. When the timeline references the rendered cut, in-points
    equal timeline positions and there is nothing to measure -- so this returns
    nothing and every zoom eases out, which is the safe behaviour anyway.
    """
    cuts = []
    for a, b in zip(v1, v1[1:]):
        removed = b["in"] - (a["in"] + (a["end"] - a["start"]))
        if removed >= min_removed_s:
            cuts.append(round(b["start"], 3))
    return cuts


# ------------------------------------------------------------- writing filters

def _filter_xml(clip_start_f, clip_end_f, keys):
    params = "".join("<keyframe><when>%d</when><value>%.4f</value></keyframe>" % (w, v)
                     for w, v in keys)
    return (
        "%s<filter><enabled>TRUE</enabled><start>%d</start><end>%d</end>"
        "<effect><name>Basic Motion</name><effectid>basic</effectid>"
        "<effecttype>motion</effecttype><mediatype>video</mediatype>"
        "<effectcategory>motion</effectcategory>"
        "<parameter><name>Scale</name><parameterid>scale</parameterid>"
        "<value>%.4f</value><valuemin>0</valuemin><valuemax>10000</valuemax>%s</parameter>"
        "<parameter><name>Center</name><parameterid>center</parameterid>"
        "<value><horiz>%.5f</horiz><vert>%.5f</vert></value></parameter>"
        "<parameter><name>Rotation</name><parameterid>rotation</parameterid>"
        "<value>0</value><valuemin>-100000</valuemin><valuemax>100000</valuemax></parameter>"
        "<parameter><name>Anchor Point</name><parameterid>centerOffset</parameterid>"
        "<value><horiz>0</horiz><vert>0</vert></value></parameter>"
        "</effect></filter>"
    ) % (MARKER, clip_start_f, clip_end_f, keys[0][1], params, 0.0, 0.0)


def clip_filter(clip_start_s, clip_end_s, ramps, fps, centre=(0.0, 0.0)):
    """The <filter> for one clip, or "" when no zoom touches it.

    Keyframe times are absolute sequence frames -- see the module docstring.
    Every clip inside a zoom gets keyframes at its own boundaries too, so the
    curve is continuous across the cut instead of restarting at each clip.
    """
    live = [r for r in ramps if r.covers(clip_start_s, clip_end_s)]
    if not live:
        return ""

    times = {clip_start_s, clip_end_s}
    for r in live:
        for p in r.breakpoints():
            if clip_start_s < p < clip_end_s:
                times.add(p)

    def value(t):
        # Zooms never overlap (the planner enforces it), so the live one wins.
        v = 100.0
        for r in live:
            v = max(v, r.at(t))
        return v

    keys = [(int(round(t * fps)), value(t)) for t in sorted(times)]
    xml = _filter_xml(int(round(clip_start_s * fps)), int(round(clip_end_s * fps)), keys)
    if centre != (0.0, 0.0):
        xml = xml.replace("<horiz>0.00000</horiz><vert>0.00000</vert>",
                          "<horiz>%.5f</horiz><vert>%.5f</vert>" % centre, 1)
    return xml


def strip_zoom_filters(xml_text):
    """Remove zoom filters, so applying zooms twice replaces rather than stacks.

    Identified by what they ARE -- a Basic Motion filter whose Scale parameter
    is keyframed -- not by the comment marker. The marker is for humans reading
    the file, and it does not survive: slide-cutout copies V1 through
    ElementTree when it merges, and a serialiser drops comments. Trusting it
    meant a second run added a second ramp on top of the first, doubling the
    keyframes on every clip. Caught by counting them, not by reading the code.
    """
    def is_zoom(block):
        return "<effectid>basic</effectid>" in block and "<keyframe>" in block

    out, i = [], 0
    for m in re.finditer(r"<filter>.*?</filter>", xml_text, re.S):
        if not is_zoom(m.group(0)):
            continue
        start = m.start()
        # take the marker with it when it is still there
        if xml_text[max(0, start - len(MARKER)):start] == MARKER:
            start -= len(MARKER)
        out.append(xml_text[i:start])
        i = m.end()
    out.append(xml_text[i:])
    return "".join(out)


def apply_to_xml(xml_text, ramps, fps, centre=(0.0, 0.0)):
    """Write the ramps into V1 of an existing timeline, returning new XML.

    Text surgery rather than a re-serialise: the file may have come from another
    skill and everything else in it must survive untouched.
    """
    xml_text = strip_zoom_filters(xml_text)
    video_open = xml_text.index("<video>")
    first = xml_text.index("<track>", video_open)
    end = xml_text.index("</track>", first)
    v1 = xml_text[first:end]

    touched = [0]

    def inject(m):
        item = m.group(0)
        st = int(re.search(r"<start>(-?\d+)</start>", item).group(1)) / fps
        en = int(re.search(r"<end>(-?\d+)</end>", item).group(1)) / fps
        f = clip_filter(st, en, ramps, fps, centre)
        if not f:
            return item
        touched[0] += 1
        return item.replace("</clipitem>", f + "</clipitem>")

    v1_new = re.sub(r"<clipitem[^>]*>.*?</clipitem>", inject, v1, flags=re.S)
    return xml_text[:first] + v1_new + xml_text[end:], touched[0]


# ----------------------------------------------------------------- reading back

def read_ramp_from_xml(xml_path):
    """Every zoom keyframe in the file, as (seconds, scale_pct), in order.

    Used by the verifier: reconstructing the curve from the finished file is the
    only way to see whether the slices actually line up, rather than trusting
    the arithmetic that produced them.
    """
    text = open(xml_path, encoding="utf-8").read()
    root = ET.parse(xml_path).getroot()
    seq = root.find("sequence")
    fps = _fps_of(seq)
    tracks = seq.findall("media/video/track")
    if not tracks:
        return [], fps, []

    points, per_clip = [], []
    for ci in tracks[0].findall("clipitem"):
        st = int(ci.findtext("start")) / fps
        en = int(ci.findtext("end")) / fps
        keys = []
        for f in ci.findall("filter"):
            if (f.findtext("effect/effectid") or "") != "basic":
                continue
            for p in f.findall("effect/parameter"):
                if (p.findtext("parameterid") or "") != "scale":
                    continue
                for k in p.findall("keyframe"):
                    keys.append((int(k.findtext("when")) / fps,
                                 float(k.findtext("value"))))
        keys.sort()
        per_clip.append({"start": st, "end": en, "keys": keys})
        points.extend(keys)
    points.sort()
    return points, fps, per_clip
