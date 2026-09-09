"""
Apply an approved cutlist to a source video and produce the skill's two
deliverables:

  <basename>_trimmed.mp4   - merged, seamless rough cut (universal, drag into
                              any editor as a single clip)
  <basename>.xml           - FCP7 XML companion timeline referencing the ORIGINAL
                              file as separate back-to-back clips, for
                              non-destructive re-editing. FCP7 XML because
                              Premiere Pro imports only that format, and Resolve
                              reads it too.

Captions are NOT produced here. They are a separate concern with its own quality
rules (line length, reading speed, sentence-aware breaks), handled by the
`captions` skill, which reads the finished trimmed video. Emitting a second,
cruder .srt from this script would put two competing caption files in the output
folder and leave the user guessing which one to import.

Usage:
    python render_cuts.py <source_video> <transcript_json> <output_dir>
        --cutlist cutlist_silence.json [--cutlist cutlist_speech.json] [--basename name]

Cut boundaries are applied exactly as given -- this script adds no padding of
its own. Padding belongs to plan_cuts.py, which is the only place it is decided.

cutlist schema (silence cuts come from plan_cuts.py; speech cuts come from
reasoning over the transcript):
{
  "cuts": [
    {"start": 0.0, "end": 2.01, "reason": "leading silence"},
    {"start": 5.10, "end": 6.30, "reason": "filler word: um"},
    ...
  ]
}
Cuts may overlap or be unsorted -- this script sorts, merges, and buffers them.
"""
import sys
import json
import argparse
import subprocess
import os
import shutil
from fractions import Fraction
from pathlib import Path

# The FCP7 XML writer is shared with the other skills in this plugin, so a fix to
# the format lands everywhere at once instead of drifting between copies.
#
# Two places it can live, and both are supported so that no source rewriting is
# needed to ship a standalone copy of this skill:
#   plugin install  -> <plugin root>/lib/fcp7xml.py  (single shared implementation)
#   standalone .skill -> alongside this script        (inlined at package time)
_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parents[2] / "lib"):
    if (_candidate / "fcp7xml.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
try:
    from fcp7xml import build_fcp7_xml, contiguous_clips
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Cannot import the FCP7 XML writer (%s). Expected it either alongside "
        "this script or at <plugin root>/lib/fcp7xml.py. Reinstall the video-post "
        "plugin, or repackage this skill with tools/build_skills.py." % exc)


def ffprobe_json(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path
    ])
    return json.loads(out)


def get_fps(video_stream):
    num, den = video_stream["r_frame_rate"].split("/")
    return Fraction(int(num), int(den))


def snap_to_frame(t, fps):
    """Return (seconds_float, exact_fraction) for time t snapped to the nearest frame boundary."""
    frame_index = round(t * fps)
    exact = Fraction(frame_index) / fps
    return float(exact), exact


def merge_cuts(cuts, duration):
    """Sort and merge overlapping cuts. Boundaries are taken as final.

    This function used to shrink every cut inward by a 350ms "safety buffer" on
    both sides. That silently destroyed the edit: any cut shorter than 700ms
    collapsed to nothing and was dropped, which killed 23 of 35 planned cuts on
    a real clip, and it also left a stray sliver of silence at the very start of
    the video. Padding is now decided once, at planning time (plan_cuts.py),
    where it can be reasoned about explicitly -- never re-applied here.
    """
    clipped = []
    for c in sorted(cuts, key=lambda c: c["start"]):
        s = max(0.0, min(duration, c["start"]))
        e = max(0.0, min(duration, c["end"]))
        if e > s:
            clipped.append({"start": s, "end": e, "reason": c.get("reason", "")})

    if not clipped:
        return []

    merged = [clipped[0]]
    for c in clipped[1:]:
        last = merged[-1]
        if c["start"] <= last["end"]:
            last["end"] = max(last["end"], c["end"])
            if c["reason"] not in last["reason"]:
                last["reason"] = last["reason"] + " + " + c["reason"]
        else:
            merged.append(c)
    return merged


def compute_keep_segments(cuts, duration):
    keep = []
    cursor = 0.0
    for c in cuts:
        if c["start"] > cursor:
            keep.append({"start": cursor, "end": c["start"]})
        cursor = max(cursor, c["end"])
    if cursor < duration:
        keep.append({"start": cursor, "end": duration})
    return [k for k in keep if k["end"] - k["start"] > 0.01]


def render_trimmed_video(source, keep_segments, out_path):
    if not keep_segments:
        raise SystemExit("No footage left after applying cuts -- refusing to render an empty video.")

    filter_parts = []
    concat_v = []
    concat_a = []
    for i, seg in enumerate(keep_segments):
        filter_parts.append(
            f"[0:v]trim=start={seg['start']:.3f}:end={seg['end']:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
        filter_parts.append(
            f"[0:a]atrim=start={seg['start']:.3f}:end={seg['end']:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )
        concat_v.append(f"[v{i}]")
        concat_a.append(f"[a{i}]")

    n = len(keep_segments)
    concat_inputs = "".join(f"{v}{a}" for v, a in zip(concat_v, concat_a))
    filter_complex = ";".join(filter_parts) + f";{concat_inputs}concat=n={n}:v=1:a=1[vout][aout]"

    cmd = [
        "ffmpeg", "-y", "-i", source,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        out_path,
    ]
    subprocess.run(cmd, check=True)


def report_summary(applied_cuts, duration, keep_segments):
    """Report what was ACTUALLY removed.

    This used to be computed from the requested cutlist rather than the cuts
    that survived processing, so it once claimed "0:38 removed" when the real
    figure was 0:16. Reporting intended work as though it were completed work
    hides exactly the kind of bug that matters, so the numbers here are derived
    from the applied cuts and the rendered keep-segments only.

    Printed to the console only, deliberately not written to a file: the user
    asked that the output folder contain nothing but files they actually load
    into an editor. Relay these numbers in your report to them instead.
    """
    total_removed = sum(c["end"] - c["start"] for c in applied_cuts)
    new_duration = sum(k["end"] - k["start"] for k in keep_segments)

    from collections import Counter
    categories = Counter()
    for c in applied_cuts:
        reason = c["reason"].split(":")[0].split(" + ")[0].strip() or "unspecified"
        categories[reason] += 1

    def fmt(t):
        m, s = divmod(int(round(t)), 60)
        return f"{m}:{s:02d}"

    lines = [f"Runtime {fmt(duration)} -> {fmt(new_duration)} ({fmt(total_removed)} removed)", ""]
    for reason, count in categories.most_common():
        lines.append(f"  {count}x {reason}")

    print("\n".join(lines))


# Filenames this pipeline is known to generate as intermediates. Pruning is
# restricted to this exact list (plus the sp_*/span snippet pattern) rather than
# a broad "*.json" glob, so a file the user happens to keep in their own folder
# is never touched.
INTERMEDIATE_NAMES = {
    "transcript.json", "out_transcript.json", "output_transcript.json",
    "cutlist.json", "cutlist_silence.json", "cutlist_speech.json",
    "inspect.py", "inspect.log", "sil_tmp.txt",
}
INTERMEDIATE_PATTERNS = ("sp_*.wav", "sp_*.json", "span_*.wav", "span_*.json")


def prune_intermediates(output_dir, deliverables):
    """Leave the output folder containing only files the user actually needs.

    Intermediates (transcripts, cutlists, extracted audio snippets) are working
    files. Sitting them next to the deliverables forces the user to guess which
    file matters -- direct feedback from a real user was that seeing stray JSON
    in the output folder was confusing. They belong in a temp directory; this is
    the backstop for when they end up here anyway.
    """
    import fnmatch
    removed, unknown = [], []
    for name in sorted(os.listdir(output_dir)):
        if name in deliverables:
            continue
        path = os.path.join(output_dir, name)
        is_intermediate = name in INTERMEDIATE_NAMES or any(
            fnmatch.fnmatch(name, pat) for pat in INTERMEDIATE_PATTERNS
        )
        if name == "__pycache__" and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed.append(name)
        elif is_intermediate and os.path.isfile(path):
            os.remove(path)
            removed.append(name)
        else:
            unknown.append(name)
    return removed, unknown


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source_video")
    ap.add_argument("transcript_json")
    ap.add_argument("output_dir")
    ap.add_argument("--cutlist", action="append", required=True,
                    help="Path to a cutlist JSON. Pass repeatedly to combine lists, e.g. "
                         "--cutlist cutlist_silence.json --cutlist cutlist_speech.json")
    ap.add_argument("--basename", default=None)
    ap.add_argument("--keep-intermediates", action="store_true",
                    help="Leave transcripts/cutlists/snippets in the output folder. Off by "
                         "default: the output folder should contain only what the user needs.")
    args = ap.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    basename = args.basename or os.path.splitext(os.path.basename(args.source_video))[0]

    probe = ffprobe_json(args.source_video)
    duration = float(probe["format"]["duration"])
    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    fps = get_fps(video_stream)

    all_cuts = []
    for path in args.cutlist:
        with open(path, encoding="utf-8") as f:
            all_cuts.extend(json.load(f)["cuts"])
    with open(args.transcript_json, encoding="utf-8") as f:
        transcript = json.load(f)

    merged_cuts = merge_cuts(all_cuts, duration)
    keep_segments = compute_keep_segments(merged_cuts, duration)

    trimmed_path = os.path.join(args.output_dir, f"{basename}_trimmed.mp4")
    xml_path = os.path.join(args.output_dir, f"{basename}.xml")

    width = int(video_stream["width"])
    height = int(video_stream["height"])
    audio_stream = next((x for x in probe["streams"] if x["codec_type"] == "audio"), None)
    audio_channels = int(audio_stream["channels"]) if audio_stream else 2

    render_trimmed_video(args.source_video, keep_segments, trimmed_path)
    # Frame-exact stacking: doing this in float seconds drifts a frame per clip.
    clips = contiguous_clips(args.source_video, keep_segments, fps)

    xml = build_fcp7_xml(
        sequence_name=basename + "_roughcut",
        fps=fps, width=width, height=height,
        video_tracks=[clips], audio_tracks=[clips],
        audio_channels=audio_channels, source_duration=duration,
    )
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml)
    report_summary(merged_cuts, duration, keep_segments)

    deliverables = {os.path.basename(x) for x in (trimmed_path, xml_path)}
    print(f"\nDelivered to {args.output_dir}:")
    for x in (trimmed_path, xml_path):
        print(f"  {os.path.basename(x)}")

    if not args.keep_intermediates:
        removed, unknown = prune_intermediates(args.output_dir, deliverables)
        if removed:
            print(f"\nPruned {len(removed)} intermediate file(s) so the folder holds only "
                  f"deliverables: {', '.join(removed)}")
        if unknown:
            print(f"\nNOTE: {len(unknown)} unrecognised file(s) left untouched (not created by "
                  f"this pipeline): {', '.join(unknown)}")
            print("If any are working files from this run, move them out -- the user should "
                  "only see finished outputs in this folder.")


if __name__ == "__main__":
    main()
