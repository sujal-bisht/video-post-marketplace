"""Find where a slow zoom is ALLOWED, so the model only has to decide where it BELONGS.

The division of labour is the same one the rough cut uses. Arithmetic does the
mechanical filtering -- what is long enough, spaced out enough, visible at all --
because that is bookkeeping across a whole video and code cannot get bored.
Choosing which of those moments deserves emphasis is a reading task, and no
threshold can do it.

The user is never asked where to zoom. Being asked is the thing that makes
automation pointless: deciding where a push-in belongs takes exactly the mental
effort that doing it by hand takes.

Usage:
    python plan_zooms.py <timeline.xml> <transcript.json> <candidates.json>
        [--min-len 8] [--max-len 14] [--min-gap 45] [--skip-head 5]
"""
import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parents[2] / "lib"):
    if (_candidate / "zoom.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
try:
    import zoom
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Cannot find zoom.py -- it should sit beside this script or at "
                     "<plugin root>/lib/zoom.py. Reinstall the video-post plugin. (%s)" % exc)

PHRASE_GAP = 0.18   # a pause this long reads as the end of a phrase


def phrase_boundaries(words):
    """Times where a phrase starts and ends, from the pauses between words."""
    starts, ends = [], []
    for i, w in enumerate(words):
        prev_gap = w["start"] - words[i - 1]["end"] if i else 99.0
        next_gap = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 99.0
        if prev_gap >= PHRASE_GAP:
            starts.append(w["start"])
        if next_gap >= PHRASE_GAP:
            ends.append(w["end"])
    return starts, ends


def nearest(values, target, lo=None, hi=None):
    pool = [v for v in values if (lo is None or v >= lo) and (hi is None or v <= hi)]
    if not pool:
        return None
    return min(pool, key=lambda v: abs(v - target))


def free_regions(duration, covered, skip_head):
    """Timeline stretches where the camera is actually the picture on screen."""
    regions, cursor = [], skip_head
    for s, e in covered:
        if s > cursor:
            regions.append((cursor, min(s, duration)))
        cursor = max(cursor, e)
    if cursor < duration:
        regions.append((cursor, duration))
    return [(s, e) for s, e in regions if e > s]


def text_between(words, a, b):
    return " ".join(w["word"].strip() for w in words if a <= w["start"] < b).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline_xml")
    ap.add_argument("transcript_json", help="Transcript of the TRIMMED video, so times "
                                            "match the timeline rather than the raw footage.")
    ap.add_argument("output_json")
    ap.add_argument("--min-len", type=float, default=8.0,
                    help="Shorter than this and a slow push has no room to be slow.")
    ap.add_argument("--max-len", type=float, default=14.0,
                    help="Longer than this and the movement stops reading as deliberate.")
    ap.add_argument("--min-gap", type=float, default=45.0,
                    help="Minimum spacing between zooms. Constant motion looks cheap.")
    ap.add_argument("--skip-head", type=float, default=5.0,
                    help="Leave the opening alone; the first words should land wide.")
    args = ap.parse_args()

    tl = zoom.read_timeline(args.timeline_xml)
    with open(args.transcript_json, encoding="utf-8") as f:
        transcript = json.load(f)
    words = [w for w in transcript["words"] if "start" in w and "end" in w]
    if not words:
        raise SystemExit("That transcript has no word timings.")

    starts, ends = phrase_boundaries(words)
    regions = free_regions(tl["duration"], tl["covered"], args.skip_head)

    candidates = []
    cursor = 0.0
    for r_start, r_end in regions:
        t = max(r_start, cursor)
        while t + args.min_len <= r_end:
            s = nearest(starts, t, lo=t - 1.0, hi=t + 4.0) or t
            # Snap to a phrase boundary, but never past the ceiling: a zoom that
            # runs long stops reading as deliberate.
            ceiling = min(s + args.max_len, r_end)
            e = nearest(ends, ceiling, lo=s + args.min_len, hi=ceiling)
            if e is None:
                break
            candidates.append({
                "start": round(s, 3), "end": round(e, 3),
                "length": round(e - s, 2),
                "text": text_between(words, s, e),
            })
            cursor = s + args.min_gap
            t = cursor

    out = {
        "duration": round(tl["duration"], 3),
        "fps": tl["fps"],
        "covered_by_slides": [[round(a, 2), round(b, 2)] for a, b in tl["covered"]],
        "real_discontinuities": zoom.real_discontinuities(tl["v1"]),
        "candidates": candidates,
        "rules": {"min_len": args.min_len, "max_len": args.max_len,
                  "min_gap": args.min_gap, "skip_head": args.skip_head},
    }
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    def fmt(t):
        return "%d:%05.2f" % (int(t // 60), t % 60)

    print("Timeline %s long, %d camera clip(s), %d span(s) covered by slides."
          % (fmt(tl["duration"]), len(tl["v1"]), len(tl["covered"])))
    print("%d eligible span(s) -- pick the ones that carry weight, skip the rest:\n"
          % len(candidates))
    for i, c in enumerate(candidates, 1):
        print("  [%d] %s -> %s  (%.1fs)" % (i, fmt(c["start"]), fmt(c["end"]), c["length"]))
        print("      %s" % (c["text"][:300] or "(no speech here)"))
    if not candidates:
        print("  none: the video is too short, or slides cover most of it.")
    print("\nWrote %s" % args.output_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
