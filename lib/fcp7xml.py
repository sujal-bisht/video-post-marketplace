"""
Build FCP7 XML (XMEML) timelines that Premiere Pro and DaVinci Resolve both
import.

WHY THIS FORMAT
---------------
Premiere Pro imports FCP7 XML and *not* FCPXML -- Adobe never added Final Cut
Pro X's format. Resolve reads FCP7 XML as well. So one format serves both
editors, and there is no reason to ship two interchange files.

WHY THIS IS SHARED CODE
-----------------------
Both the rough cut (one video track of clips from one source) and the slide
cutout (camera, slide stills, and a circular overlay on separate tracks) need to
emit this format. Written twice, the two copies drift: a fix lands in one and is
forgotten in the other. One implementation, used by every skill in this plugin.

WHAT THE FORMAT DEMANDS
-----------------------
- **Whole frames, not seconds.** Every time is an integer frame number, so all
  input seconds are rounded to the nearest frame. A boundary can therefore move
  by up to half a frame against the rendered video: imperceptible, and
  unavoidable in a frame-addressed format.
- **An integer timebase plus an NTSC flag**, rather than a rational rate. 29.97
  is timebase 30 with ntsc TRUE.
- **Each media file described once**, then referenced by id. Repeating the full
  <file> block per clip is legal but bloats the document, and some importers
  then treat every copy as a separate media item in the project bin.
- **Gaps need no filler.** A track is a list of clips with start/end positions;
  anything not covered is simply empty. That is what lets a slide appear for
  twenty seconds and then vanish.

Usage:
    from fcp7xml import Clip, build_fcp7_xml

    xml = build_fcp7_xml(
        sequence_name="lesson_01",
        fps=Fraction(30, 1), width=1920, height=1080,
        video_tracks=[camera_clips, slide_clips, overlay_clips],  # V1, V2, V3
        audio_tracks=[camera_audio_clips],
        audio_channels=2,
    )
"""
import os
from dataclasses import dataclass
from fractions import Fraction
from urllib.parse import quote
from xml.sax.saxutils import escape


@dataclass
class Clip:
    """One clip on a track.

    path        absolute path to the media file
    start/end   position on the TIMELINE, in seconds
    source_in   in-point within the source file, in seconds (0 for stills)
    name        label shown in the editor; defaults to the filename
    still       True for a PNG/JPG rather than a movie
    alpha       True if the file carries transparency that should be honoured
                (a circular cutout overlay); emits <alphatype>straight</alphatype>
    width/height  the media's own dimensions; defaults to the sequence size
    """
    path: str
    start: float
    end: float
    source_in: float = 0.0
    name: str = None
    still: bool = False
    alpha: bool = False
    width: int = None
    height: int = None


def fps_to_timebase(fps):
    """FCP7 XML wants an integer timebase plus an NTSC flag, not a rational rate:
    29.97 is expressed as timebase 30 with ntsc TRUE."""
    if fps.denominator == 1001:
        return int(round(float(fps))), "TRUE"
    return int(round(float(fps))), "FALSE"


def _pathurl(path):
    """FCP7 XML expects a file URL. Percent-encode it, since real paths contain
    spaces, but leave the drive colon and separators intact."""
    return "file://localhost/" + quote(os.path.abspath(path).replace(os.sep, "/"),
                                       safe="/:")


def _rate_block(timebase, ntsc):
    return "<rate><timebase>%d</timebase><ntsc>%s</ntsc></rate>" % (timebase, ntsc)


class _FileRegistry:
    """Emits a full <file> definition the first time a path is used and a bare
    reference every time after, keeping ids stable across all tracks."""

    def __init__(self, rate, seq_width, seq_height, audio_channels, still_frames):
        self._ids = {}
        self._rate = rate
        self._w = seq_width
        self._h = seq_height
        self._channels = audio_channels
        self._still_frames = still_frames

    def reference(self, clip, total_frames):
        path = os.path.abspath(clip.path)
        if path in self._ids:
            return '<file id="%s"/>' % self._ids[path]

        file_id = "file%d" % (len(self._ids) + 1)
        self._ids[path] = file_id

        name = escape(clip.name or os.path.basename(path))
        width = clip.width or self._w
        height = clip.height or self._h
        # A still has no intrinsic length, so give it a generous nominal duration;
        # the clipitem's in/out decide how long it actually shows.
        duration = self._still_frames if clip.still else total_frames

        alpha = "<alphatype>straight</alphatype>" if clip.alpha else ""
        # Stills carry no audio; declaring an audio stream on one makes some
        # importers look for a track that does not exist.
        audio = "" if clip.still else "<audio><channelcount>%d</channelcount></audio>" % self._channels

        return (
            '<file id="%s"><name>%s</name><pathurl>%s</pathurl>%s'
            "<duration>%d</duration><media>"
            "<video><samplecharacteristics>%s<width>%d</width><height>%d</height>"
            "<pixelaspectratio>square</pixelaspectratio></samplecharacteristics>%s</video>"
            "%s</media></file>"
        ) % (file_id, name, _pathurl(path), self._rate, duration,
             self._rate, width, height, alpha, audio)


def contiguous_clips(path, segments, fps, **clip_kwargs):
    """Lay source segments end to end with frame-exact lengths.

    Accumulating timeline positions in float seconds and rounding later loses a
    frame here and there: a 13-clip test drifted by up to one frame per clip and
    finished a frame short of the rendered video. Doing the arithmetic in whole
    frames and only converting back to seconds at the end keeps every clip
    exactly as long as the source frames it covers, with nothing to accumulate.

    segments is a list of {"start": seconds, "end": seconds} in SOURCE time.
    """
    fps_f = float(Fraction(fps) if not isinstance(fps, Fraction) else fps)

    def fr(t):
        return int(round(t * fps_f))

    clips = []
    cursor = 0  # frames
    for seg in segments:
        s_in, s_out = fr(seg["start"]), fr(seg["end"])
        length = s_out - s_in
        if length <= 0:
            continue
        clips.append(Clip(path=path,
                          start=cursor / fps_f,
                          end=(cursor + length) / fps_f,
                          source_in=s_in / fps_f,
                          **clip_kwargs))
        cursor += length
    return clips


def build_fcp7_xml(sequence_name, fps, width, height, video_tracks,
                   audio_tracks=(), audio_channels=2, source_duration=None,
                   still_duration_s=3600):
    """Return an FCP7 XML document as a string.

    video_tracks / audio_tracks are lists of tracks; each track is a list of
    Clip. Track order is V1, V2, V3 (and A1, A2), bottom-most first, matching
    how an editor stacks layers.

    source_duration is the length in seconds of the movie files being
    referenced. It only fills in each <file>'s declared duration, so an
    approximate value is harmless, but passing the real one keeps the editor's
    project bin honest.
    """
    if isinstance(fps, (int, float)):
        fps = Fraction(fps).limit_denominator(1000)
    timebase, ntsc = fps_to_timebase(fps)
    fps_f = float(fps)
    rate = _rate_block(timebase, ntsc)

    def frames(seconds):
        return int(round(seconds * fps_f))

    total_frames = frames(source_duration) if source_duration else frames(3600)
    still_frames = frames(still_duration_s)
    registry = _FileRegistry(rate, width, height, audio_channels, still_frames)

    counter = {"n": 0}

    def render_clip(clip, is_audio):
        counter["n"] += 1
        cid = ("a%d" if is_audio else "v%d") % counter["n"]
        t_start, t_end = frames(clip.start), frames(clip.end)
        length = t_end - t_start
        if length <= 0:
            return None
        s_in = 0 if clip.still else frames(clip.source_in)
        s_out = s_in + length
        name = escape(clip.name or os.path.basename(clip.path))
        file_ref = registry.reference(clip, total_frames)
        source_track = ("<sourcetrack><mediatype>audio</mediatype>"
                        "<trackindex>1</trackindex></sourcetrack>") if is_audio else ""
        return (
            '        <clipitem id="%s"><name>%s</name>%s'
            "<start>%d</start><end>%d</end><in>%d</in><out>%d</out>"
            "<enabled>TRUE</enabled>%s%s</clipitem>"
        ) % (cid, name, rate, t_start, t_end, s_in, s_out, file_ref, source_track)

    def render_track(clips, is_audio):
        items = [render_clip(c, is_audio) for c in sorted(clips, key=lambda c: c.start)]
        items = [i for i in items if i]
        return "        <track>\n%s\n        </track>" % "\n".join(items) if items else \
               "        <track/>"

    # Files must be fully defined on first appearance in document order, so video
    # tracks are rendered before audio ones -- the same order they are written out.
    video_xml = "\n".join(render_track(t, False) for t in video_tracks)
    audio_xml = "\n".join(render_track(t, True) for t in audio_tracks)

    seq_end = 0
    for track in list(video_tracks) + list(audio_tracks):
        for c in track:
            seq_end = max(seq_end, frames(c.end))

    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5">
  <sequence id="sequence1">
    <name>{name}</name>
    <duration>{duration}</duration>
    {rate}
    <timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>
    <media>
      <video>
        <format><samplecharacteristics>{rate}<width>{width}</width><height>{height}</height><pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>
{video}
      </video>
      <audio>
        <format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format>
{audio}
      </audio>
    </media>
  </sequence>
</xmeml>
""".format(name=escape(sequence_name), duration=seq_end, rate=rate,
           width=width, height=height, video=video_xml, audio=audio_xml)
