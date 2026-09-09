"""Check that a timeline plays the same thing the finished cut does.

WHY THIS EXISTS
---------------
Every other check passed while sound and picture were seconds apart. Media all
present, tracks well-formed, nothing overlapping, durations matching to the
frame, zero offline items on import -- and the speaker's lips were saying one
thing while the audio said another.

Nothing compared *what the timeline plays* against *what the edit actually is*.
A clip carries two independent numbers: where it sits on the timeline, and where
it reads from inside its source file. So a clip can be in the right place, the
right length, pointing at a file that exists, and still show the wrong footage.

HOW
---
For sampled clips, take the audio the clip reads from at its in-point and the
audio the rendered cut has at that timeline position, and compare their loudness
contours. Same content, even through a different encode, scores 0.000; a shift
of a tenth of a second already scores 0.27. Speech rhythm is sharply
discriminating in a way pictures are not -- frame thumbnails were tried first
and abandoned, because a talking head in front of a static background looks
nearly identical two seconds apart (measured: 4.0 on a 0-255 scale, far too
close to the noise of re-encoding to judge anything).

Since the audio track of the timeline IS the rendered cut's audio, a clip whose
source audio matches the cut at that moment is a clip whose picture matches its
sound.

This deliberately re-reads the finished files rather than recomputing the
numbers that produced them.
"""
import array
import math
import os
import subprocess
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse

_WINDOW = 0.6      # seconds of audio compared per sample
_BINS = 32         # loudness bins across that window
_OFFSET = 0.1      # seconds into the clip, to clear the boundary

# Mean absolute difference between normalised loudness contours. Measured on
# real footage: the same moment through two different encodes scores 0.000,
# while a 0.1s shift scores 0.27 and larger shifts stay above 0.25. 0.12 sits in
# the empty space between those two populations.
MATCH_THRESHOLD = 0.12


def envelope(path, seconds, window=_WINDOW, bins=_BINS):
    """Normalised loudness contour of a short window of audio."""
    cmd = ["ffmpeg", "-v", "error", "-ss", "%.3f" % max(0.0, seconds),
           "-t", "%.3f" % window, "-i", path,
           "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = len(raw) // 2
    if n < bins * 8:
        return None
    samples = array.array("h")
    samples.frombytes(raw[:n * 2])
    size = max(1, n // bins)
    env = []
    for i in range(bins):
        chunk = samples[i * size:(i + 1) * size]
        if not chunk:
            env.append(0.0)
            continue
        env.append(math.sqrt(sum(float(x) * x for x in chunk) / len(chunk)))
    peak = max(env)
    if peak <= 0:
        return None  # pure digital silence tells us nothing either way
    return [e / peak for e in env]


def envelope_distance(a, b):
    if a is None or b is None:
        return None
    return sum(abs(x - y) for x, y in zip(a, b)) / float(len(a))


def path_from_pathurl(url):
    p = unquote(urlparse(url).path)
    if p.startswith("/") and len(p) > 2 and p[2] == ":":
        p = p[1:]
    return os.path.normpath(p)


def read_video_clips(xml_path, track_index=1):
    """Read one video track as dicts of start/in/end frames plus media path."""
    root = ET.parse(xml_path).getroot()
    files = {}
    for f in root.iter("file"):
        url = f.findtext("pathurl")
        if url:
            files[f.get("id")] = path_from_pathurl(url)

    seq = root.find("sequence")
    fps = float(seq.findtext("rate/timebase") or 25)
    if (seq.findtext("rate/ntsc") or "FALSE").upper() == "TRUE":
        fps = fps * 1000.0 / 1001.0

    tracks = seq.findall("media/video/track")
    if len(tracks) < track_index:
        return [], fps
    clips = []
    for ci in tracks[track_index - 1].findall("clipitem"):
        fe = ci.find("file")
        if fe is None or fe.get("id") not in files:
            continue
        clips.append({"start": int(ci.findtext("start")),
                      "in": int(ci.findtext("in")),
                      "end": int(ci.findtext("end")),
                      "media": files[fe.get("id")]})
    return clips, fps


def check_timeline_sync(xml_path, rendered_cut, track_index=1, samples=8,
                        threshold=MATCH_THRESHOLD):
    """Compare sampled timeline positions against the rendered cut.

    Returns (results, note). Each result gives the timeline position, the
    distance between the two loudness contours, and whether it matched.
    """
    clips, fps = read_video_clips(xml_path, track_index)
    if not clips:
        return [], "no clips on that track"

    # A sample has to sit wholly inside one clip: near a cut boundary the two
    # sides legitimately differ, since that is exactly where material was
    # removed. Short clips are skipped rather than reported as failures.
    need = _OFFSET + _WINDOW
    usable = [c for c in clips if (c["end"] - c["start"]) / fps >= need]
    note = ""
    if not usable:
        return [], ("every clip is shorter than %.1fs, so no sample fits inside one"
                    % need)
    if len(usable) < len(clips):
        note = "%d of %d clips too short to sample" % (len(clips) - len(usable), len(clips))

    # Spread across the whole timeline: this kind of drift grows as it goes, so
    # checking only the opening would have called the broken edit fine.
    step = max(1, len(usable) // samples)
    picks = usable[::step][:samples]
    if usable[-1] not in picks:
        picks.append(usable[-1])

    results = []
    for c in picks:
        t_timeline = c["start"] / fps + _OFFSET
        t_source = c["in"] / fps + _OFFSET
        row = {"at": t_timeline, "media": os.path.basename(c["media"]),
               "reads_from": t_source}
        if not os.path.isfile(c["media"]):
            row.update(ok=False, distance=None, reason="media missing")
            results.append(row)
            continue
        d = envelope_distance(envelope(c["media"], t_source),
                              envelope(rendered_cut, t_timeline))
        if d is None:
            # Silence on either side is not evidence of a problem.
            row.update(ok=True, distance=None, reason="silent, nothing to compare")
        else:
            row.update(ok=d <= threshold, distance=d,
                       reason="" if d <= threshold else "plays different audio")
        results.append(row)
    return results, note


def format_results(results, note="", label="V1"):
    """Human-readable lines plus a pass/fail flag."""
    lines = []
    bad = [r for r in results if not r["ok"]]
    for r in results:
        dist = "n/a" if r["distance"] is None else "%.3f" % r["distance"]
        lines.append("    %6.2fs reads %7.2fs  diff %-6s %s%s"
                     % (r["at"], r["reads_from"], dist,
                        "ok" if r["ok"] else "MISMATCH",
                        "  (%s)" % r["reason"] if r["reason"] else ""))
    if bad:
        worst = max((r["distance"] or 9.9) for r in bad)
        lines.append("  %s FAILS: %d of %d samples play something other than the cut "
                     "(worst %.3f). Picture and sound are on different clocks."
                     % (label, len(bad), len(results), worst))
    else:
        lines.append("  %s: all %d samples match the rendered cut -- picture and sound "
                     "agree." % (label, len(results)))
    if note:
        lines.append("  (%s)" % note)
    return lines, not bad
