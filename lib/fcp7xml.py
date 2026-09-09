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
Both the rough cut (clips from one source, end to end) and the slide cutout
(camera, slides, and a circular overlay stacked on separate tracks) need to emit
this format. Written twice, the two copies drift: a fix lands in one and is
forgotten in the other. One implementation, used by every skill in this plugin.

WHAT THE FORMAT DEMANDS
-----------------------
- **Whole frames, not seconds.** Every time is an integer frame number, so all
  input seconds are rounded to the nearest frame. A boundary can therefore move
  by up to half a frame against the rendered video: imperceptible, and
  unavoidable in a frame-addressed format.
- **An integer timebase plus an NTSC flag**, rather than a rational rate. 29.97
  is timebase 30 with ntsc TRUE.
- **Audio essence has to be described, not just counted.** A `<channelcount>`
  alone is not enough: without `<samplecharacteristics>` giving depth and sample
  rate, Resolve imports the clip and then shows the audio offline. Learned the
  hard way -- video linked fine while audio came up red.
- **One audio track per channel.** A stereo source is expressed as two audio
  clipitems, `<trackindex>` 1 and 2, not one clipitem claiming two channels.
- **Only declare audio on files that have it.** A rendered overlay has no audio
  stream; saying it has two channels sends the importer looking for essence that
  does not exist.
- **`<link>` ties video to its audio.** Without links the editor treats them as
  unrelated clips, so dragging a clip leaves its sound behind -- which defeats
  the point of handing over an adjustable timeline.
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
        audio_clips=camera_clips,        # fanned out across channels
        audio_channels=2, audio_sample_rate=48000,
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
    still       True for a PNG/JPG rather than a movie; gets a nominal duration
    alpha       True if the file carries transparency to honour (a cutout
                overlay); emits <alphatype>straight</alphatype>
    has_audio   whether the file actually contains an audio stream. Rendered
                overlays and stills do not; claiming otherwise makes importers
                hunt for essence that is not there.
    width/height  the media's own dimensions; defaults to the sequence size
    """
    path: str
    start: float
    end: float
    source_in: float = 0.0
    name: str = None
    still: bool = False
    alpha: bool = False
    has_audio: bool = True
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

    def __init__(self, rate, seq_width, seq_height, audio_channels,
                 still_frames, sample_rate, depth):
        self._ids = {}
        self._rate = rate
        self._w = seq_width
        self._h = seq_height
        self._channels = audio_channels
        self._still_frames = still_frames
        self._sample_rate = sample_rate
        self._depth = depth

    def file_id(self, clip):
        return self._ids.get(os.path.abspath(clip.path))

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

        # Describe the audio essence properly, and only when it exists. Omitting
        # samplecharacteristics is what made audio import offline.
        audio = ""
        if clip.has_audio and not clip.still:
            audio = (
                "<audio><samplecharacteristics>"
                "<depth>%d</depth><samplerate>%d</samplerate>"
                "</samplecharacteristics><channelcount>%d</channelcount></audio>"
            ) % (self._depth, self._sample_rate, self._channels)

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


def _link_block(members):
    """A <link> group naming every clipitem that belongs to one logical clip, so
    the editor moves picture and sound together."""
    return "".join(
        "<link><linkclipref>%s</linkclipref><mediatype>%s</mediatype>"
        "<trackindex>%d</trackindex><clipindex>%d</clipindex></link>"
        % (cid, mediatype, trackindex, clipindex)
        for cid, mediatype, trackindex, clipindex in members)


def build_fcp7_xml(sequence_name, fps, width, height, video_tracks,
                   audio_clips=(), audio_channels=2, source_duration=None,
                   still_duration_s=3600, audio_sample_rate=48000, audio_depth=16):
    """Return an FCP7 XML document as a string.

    video_tracks is a list of tracks (V1, V2, V3 -- bottom-most first, matching
    how an editor stacks layers); each track is a list of Clip.

    audio_clips is the single list of clips carrying sound, usually the camera
    clips from V1. They are fanned out across `audio_channels` tracks, because
    the format expresses a stereo source as one clipitem per channel rather than
    one clipitem claiming two.

    source_duration is the length in seconds of the movie files referenced; it
    fills in each <file>'s declared duration.
    """
    if isinstance(fps, (int, float)):
        fps = Fraction(fps).limit_denominator(1000)
    timebase, ntsc = fps_to_timebase(fps)
    fps_f = float(fps)
    rate = _rate_block(timebase, ntsc)

    def frames(seconds):
        return int(round(seconds * fps_f))

    total_frames = frames(source_duration) if source_duration else frames(3600)
    registry = _FileRegistry(rate, width, height, audio_channels,
                             frames(still_duration_s), audio_sample_rate, audio_depth)

    audio_clips = list(audio_clips)

    # Ids are assigned up front so <link> groups can name clipitems that appear
    # later in the document.
    video_ids = [["v%d_%d" % (t + 1, i + 1) for i in range(len(track))]
                 for t, track in enumerate(video_tracks)]
    audio_ids = [["a%d_%d" % (ch + 1, i + 1) for i in range(len(audio_clips))]
                 for ch in range(audio_channels)]

    # Every camera clip on V1 links to its audio counterparts on each channel.
    links_for_v1 = []
    for i in range(len(video_tracks[0]) if video_tracks else 0):
        members = [(video_ids[0][i], "video", 1, i + 1)]
        if i < len(audio_clips):
            for ch in range(audio_channels):
                members.append((audio_ids[ch][i], "audio", ch + 1, i + 1))
        links_for_v1.append(_link_block(members) if len(members) > 1 else "")

    def clipitem(cid, clip, extra=""):
        t_start, t_end = frames(clip.start), frames(clip.end)
        length = t_end - t_start
        if length <= 0:
            return None
        s_in = 0 if clip.still else frames(clip.source_in)
        s_out = s_in + length
        name = escape(clip.name or os.path.basename(clip.path))
        file_ref = registry.reference(clip, total_frames)
        return (
            '        <clipitem id="%s"><name>%s</name>%s'
            "<start>%d</start><end>%d</end><in>%d</in><out>%d</out>"
            "<enabled>TRUE</enabled>%s%s</clipitem>"
        ) % (cid, name, rate, t_start, t_end, s_in, s_out, file_ref, extra)

    # Video tracks first, so each file is fully defined before any audio clipitem
    # refers to it by id.
    video_xml = []
    for t, track in enumerate(video_tracks):
        items = []
        for i, clip in enumerate(sorted(track, key=lambda c: c.start)):
            extra = links_for_v1[i] if (t == 0 and i < len(links_for_v1)) else ""
            item = clipitem(video_ids[t][i], clip, extra)
            if item:
                items.append(item)
        video_xml.append("        <track>\n%s\n        </track>" % "\n".join(items)
                         if items else "        <track/>")

    audio_xml = []
    for ch in range(audio_channels):
        items = []
        for i, clip in enumerate(audio_clips):
            source_track = ("<sourcetrack><mediatype>audio</mediatype>"
                            "<trackindex>%d</trackindex></sourcetrack>" % (ch + 1))
            item = clipitem(audio_ids[ch][i], clip,
                            source_track + (links_for_v1[i] if i < len(links_for_v1) else ""))
            if item:
                items.append(item)
        audio_xml.append("        <track>\n%s\n        </track>" % "\n".join(items)
                         if items else "        <track/>")

    seq_end = 0
    for track in list(video_tracks) + [audio_clips]:
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
        <format><samplecharacteristics><depth>{depth}</depth><samplerate>{sample_rate}</samplerate></samplecharacteristics></format>
{audio}
      </audio>
    </media>
  </sequence>
</xmeml>
""".format(name=escape(sequence_name), duration=seq_end, rate=rate,
           width=width, height=height, depth=audio_depth,
           sample_rate=audio_sample_rate,
           video="\n".join(video_xml), audio="\n".join(audio_xml))
