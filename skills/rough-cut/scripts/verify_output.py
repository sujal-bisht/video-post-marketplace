"""
Measure the rendered output and prove the cut actually worked.

WHY THIS SCRIPT EXISTS
----------------------
A rough cut was once delivered claiming "38s of dead air removed" when the real
figure was 16s, and roughly 90s of dead air was still sitting in the video --
including a silent gap at the very start. Nothing in the pipeline had checked
the finished file, so the failure reached the user instead of the developer.

This script closes that hole: it re-measures the actual output the same way
silence was detected in the first place, and reports what is really left. Run it
after every render and report ITS numbers -- never the numbers you intended.

Exit code is 1 if the output still fails the dead-air bar, so a bad render is
noisy instead of silent.

Usage:
    python verify_output.py <rendered_video> [--original <source_video>]
        [--max-gap-ms 150] [--noise-db -40] [--min-silence 0.12]
"""
import re
import sys
from pathlib import Path
import json
import argparse
import subprocess


_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parents[2] / "lib"):
    if (_candidate / "sync_check.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
try:
    import sync_check
except ImportError:  # pragma: no cover
    sync_check = None


def probe_duration(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path
    ])
    return float(out.strip())


def detect_silences(path, noise_db, min_silence):
    proc = subprocess.run(
        ["ffmpeg", "-i", path, "-af",
         f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    duration = probe_duration(path)
    regions = []
    start = None
    for line in proc.stderr.splitlines():
        m = re.search(r"silence_start:\s*(-?[0-9.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m and start is not None:
            regions.append((start, float(m.group(1))))
            start = None
    if start is not None:
        regions.append((start, duration))
    return duration, regions


def fmt(t):
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:04.1f}"


def find_repeated_phrases(transcript_path, phrase_len=4):
    """Look for the same run of words appearing twice close together in the
    FINISHED cut -- the signature of a restart that was left in.

    This check exists because a restart can be completely invisible in the
    original transcript: Whisper collapsed a whole restated sentence into a
    single bogus 4.64s word ("one"), so reading the transcript could not reveal
    it. Once the dead air around it is removed, the duplicate becomes obvious in
    a fresh transcript of the output. So: transcribe the result and look again.
    """
    with open(transcript_path, encoding="utf-8") as f:
        words = json.load(f)["words"]

    tokens = [(re.sub(r"[^a-z']", "", w["word"].lower()), w) for w in words]
    tokens = [(t, w) for t, w in tokens if t]

    hits = []
    seen = {}
    for i in range(len(tokens) - phrase_len + 1):
        phrase = " ".join(t for t, _ in tokens[i:i + phrase_len])
        if phrase in seen:
            prev_i = seen[phrase]
            # only flag near-duplicates; the same phrase recurring a minute
            # later is normal speech, not a restart
            if i - prev_i <= 25:
                hits.append((phrase, tokens[prev_i][1]["start"], tokens[i][1]["start"]))
        seen[phrase] = i
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rendered_video")
    ap.add_argument("--original", default=None,
                    help="Source video, to report the before/after comparison.")
    ap.add_argument("--max-gap-ms", type=float, default=150,
                    help="Longest internal silence considered acceptable in the output.")
    ap.add_argument("--noise-db", type=float, default=-45.0)
    ap.add_argument("--min-silence", type=float, default=0.12)
    ap.add_argument("--check-transcript", default=None,
                    help="Transcript JSON of the RENDERED output (produced by re-running "
                         "transcribe.py on it). Enables the leftover-restart check.")
    ap.add_argument("--xml", default=None,
                    help="The companion timeline. Checks that its clips actually play what "
                         "the rendered cut plays -- the one failure that every other check "
                         "here sails past, because a clip can be the right length in the "
                         "right place and still read from the wrong part of its source.")
    args = ap.parse_args()

    max_gap = args.max_gap_ms / 1000.0
    duration, regions = detect_silences(args.rendered_video, args.noise_db, args.min_silence)

    total_silence = sum(e - s for s, e in regions)
    offenders = [(s, e) for s, e in regions if (e - s) > max_gap]

    # A gap at the very front is the worst kind: the video should open on speech.
    lead = next(((s, e) for s, e in regions if s <= 0.15), None)

    print(f"Output: {args.rendered_video}")
    if args.original:
        orig_duration = probe_duration(args.original)
        saved = orig_duration - duration
        print(f"Runtime: {fmt(orig_duration)} -> {fmt(duration)} "
              f"({fmt(saved)} removed, {100 * saved / orig_duration:.0f}%)")
    else:
        print(f"Runtime: {fmt(duration)}")

    print(f"Dead air remaining: {total_silence:.1f}s across {len(regions)} regions "
          f"({100 * total_silence / duration:.0f}% of output)")

    ok = True
    if lead:
        print(f"FAIL: video opens with {lead[1] - lead[0]:.2f}s of silence before the first word "
              f"-- it should start on speech.")
        ok = False

    if offenders:
        ok = False
        print(f"FAIL: {len(offenders)} internal gaps exceed {args.max_gap_ms:.0f}ms:")
        for s, e in offenders[:15]:
            print(f"    {fmt(s)} -> {fmt(e)}   ({e - s:.2f}s)")
        if len(offenders) > 15:
            print(f"    ... and {len(offenders) - 15} more")

    if args.check_transcript:
        repeats = find_repeated_phrases(args.check_transcript)
        if repeats:
            # Advisory, not a failure: deliberate parallelism reads identically to
            # a restart here ("what Tuesday's post is for" / "Tuesday's post is for
            # nothing" is rhetoric, not a stumble). Judgment is required, so these
            # are surfaced for inspection rather than auto-failed or auto-cut.
            print(f"\nREVIEW: {len(repeats)} repeated phrase(s) in the cut. Each is either a "
                  f"missed restart (cut the earlier one) or deliberate parallelism (leave it). "
                  f"Listen to each before deciding:")
            for phrase, first, second in repeats[:10]:
                print(f"    {fmt(first)} and {fmt(second)}: \"{phrase}\"")
        else:
            print("No leftover repeated phrases (no obvious missed restarts).")

    if args.xml:
        if sync_check is None:
            print("\nFAIL: cannot check timeline sync -- sync_check.py was not found next to "
                  "this script or at <plugin root>/lib/. Reinstall the video-post plugin.")
            ok = False
        else:
            results, note = sync_check.check_timeline_sync(args.xml, args.rendered_video)
            print("\nTimeline vs rendered cut:")
            if not results:
                print("  could not sample the timeline (%s)" % (note or "no clips"))
            else:
                lines, synced = sync_check.format_results(results, note)
                print("\n".join(lines))
                ok = ok and synced

    if ok:
        print(f"PASS: no gap exceeds {args.max_gap_ms:.0f}ms and the video opens on speech.")
        return 0

    print("\nThe render did not meet the dead-air bar. Do not report this as finished --  "
          "investigate before handing it over.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
