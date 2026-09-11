"""Lay out the zoom cadence, so the model only decides which moments carry it.

The division of labour is the one the rough cut uses. Arithmetic does the
mechanical work -- cadence, length, spacing, what is visible at all -- because
that is bookkeeping across a whole video and code cannot get bored. Choosing
which moment deserves the emphasis is a reading task, and no threshold does it.

The user is never asked where to zoom. Being asked is the thing that makes the
automation pointless: deciding where a push-in belongs costs exactly the mental
effort that doing it by hand costs.

WHAT THE CADENCE IS, AND WHY THESE NUMBERS
------------------------------------------
The first version was far too sparse, and it was obvious the moment a real video
came back: on a 3-minute clip the first zoom arrived at 0:57, the next at 1:44,
the third at 2:30. Forty-seven second gaps, and only 20% of the runtime had any
movement in it. It read as three isolated events rather than as a style.

So the target is stated as COVERAGE -- roughly half the runtime should be
moving -- and the rest follows from it:

    zoom length      12-16s     long enough to feel like a drift, not a move
    quiet between    14s        the floor; less and the frame never settles
    coverage         ~50%       14s of zoom + 14s of stillness = half and half
    first zoom       by 0:20    a video that opens static stays static

Coverage is the number to reason about; length and spacing are only the knobs
that produce it, and they have to agree. Half the runtime moving means the
zooms and the quiet stretches are about equal, so a 14s gap needs a 14s zoom.
Shorter zooms with the same gap land nearer 40%, which is what the first
attempt did.

Coverage is measured against ELIGIBLE time, not total runtime: the stretches
where a slide covers the camera cannot move whatever the setting says.

Usage:
    python plan_zooms.py <timeline.xml> <transcript.json> <candidates.json>
        [--min-len 12] [--max-len 16] [--min-quiet 14] [--coverage 0.5]
        [--skip-head 4] [--first-by 20]
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
    ap.add_argument("--min-len", type=float, default=12.0,
                    help="Shorter than this and a slow push has no room to be slow.")
    ap.add_argument("--max-len", type=float, default=16.0,
                    help="Longer than this and one move outstays its welcome.")
    ap.add_argument("--min-quiet", type=float, default=14.0,
                    help="Seconds of stillness between zooms. This is the floor, not the "
                         "target: below it the frame never settles between moves.")
    ap.add_argument("--coverage", type=float, default=0.5,
                    help="Fraction of the runtime that should be moving. This is the number "
                         "to reason about; length and spacing are only the knobs that "
                         "produce it.")
    ap.add_argument("--skip-head", type=float, default=4.0,
                    help="Leave the very opening alone; the first words land wide.")
    ap.add_argument("--first-by", type=float, default=20.0,
                    help="The first zoom should begin by this time. A video that opens "
                         "static reads as static however good the rest is.")
    args = ap.parse_args()

    tl = zoom.read_timeline(args.timeline_xml)
    with open(args.transcript_json, encoding="utf-8") as f:
        transcript = json.load(f)
    words = [w for w in transcript["words"] if "start" in w and "end" in w]
    if not words:
        raise SystemExit("That transcript has no word timings.")

    starts, ends = phrase_boundaries(words)
    regions = free_regions(tl["duration"], tl["covered"], args.skip_head)

    # Walk the cadence: a zoom, a quiet stretch, a zoom. Each one starts and ends
    # on a phrase boundary so the movement begins with a thought rather than
    # mid-word.
    candidates = []
    for r_start, r_end in regions:
        t = r_start
        while t + args.min_len <= r_end:
            # Snapping to a phrase start may not reach back before the region:
            # a zoom that begins a second early begins underneath the slide,
            # where nothing it does can be seen.
            s = nearest(starts, t, lo=max(r_start, t - 1.0), hi=t + 3.0) or t
            ceiling = min(s + args.max_len, r_end)
            e = nearest(ends, ceiling, lo=s + args.min_len, hi=ceiling)
            if e is None:
                # Speech does not always pause where the cadence wants it to --
                # on real footage the longest run without a phrase break was 9.3s
                # against a 4s window. A small overshoot beats losing the zoom,
                # and losing it used to take the whole region with it, which is
                # how one video came back with no zooms at all. The overshoot is
                # capped: a first attempt at +4s produced an 18.9s zoom against a
                # stated 16s ceiling, which is not a ceiling.
                e = nearest(ends, ceiling, lo=s + args.min_len,
                            hi=min(s + args.max_len + 2.0, r_end))
            if e is None and ceiling - s >= args.min_len:
                e = ceiling                   # no pause to land on; cut to time
            if e is None or e - s < args.min_len:
                t = s + args.min_len          # step on; never abandon the region
                continue
            candidates.append({
                "start": round(s, 3), "end": round(e, 3),
                "length": round(e - s, 2),
                "text": text_between(words, s, e),
            })
            t = e + args.min_quiet

    eligible = sum(e - s for s, e in regions)
    moving = sum(c["length"] for c in candidates)
    full_coverage = moving / eligible if eligible else 0.0
    # How many of these have to survive the judgment pass to hit the target.
    avg_len = (moving / len(candidates)) if candidates else 0.0
    keep_at_least = int(round(args.coverage * eligible / avg_len)) if avg_len else 0
    keep_at_least = max(1, min(keep_at_least, len(candidates)))

    out = {
        "duration": round(tl["duration"], 3),
        "fps": tl["fps"],
        "covered_by_slides": [[round(a, 2), round(b, 2)] for a, b in tl["covered"]],
        "real_discontinuities": zoom.real_discontinuities(tl["v1"]),
        "candidates": candidates,
        "coverage_if_all_kept": round(full_coverage, 3),
        "coverage_target": args.coverage,
        "keep_at_least": keep_at_least,
        "rules": {"min_len": args.min_len, "max_len": args.max_len,
                  "min_quiet": args.min_quiet, "skip_head": args.skip_head,
                  "first_by": args.first_by, "coverage": args.coverage},
    }
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    def fmt(t):
        return "%d:%05.2f" % (int(t // 60), t % 60)

    print("Timeline %s, %d camera clip(s), %.0fs of it eligible (%d span(s) under slides)."
          % (fmt(tl["duration"]), len(tl["v1"]), eligible, len(tl["covered"])))
    print("%d candidate(s) on the cadence -- keeping all of them gives %.0f%% coverage; "
          "the target is %.0f%%." % (len(candidates), 100 * full_coverage,
                                     100 * args.coverage))
    print("So keep AT LEAST %d of them. Drop only the ones that carry nothing.\n"
          % keep_at_least)

    for i, c in enumerate(candidates, 1):
        print("  [%d] %s -> %s  (%.1fs)" % (i, fmt(c["start"]), fmt(c["end"]), c["length"]))
        print("      %s" % (c["text"][:300] or "(no speech here)"))

    if not candidates:
        print("  none: the video is too short, or slides cover most of it.")
    elif candidates[0]["start"] > args.first_by:
        print("\nnote: the earliest possible zoom is %s, later than the %.0fs mark. Usually "
              "means a slide covers the opening." % (fmt(candidates[0]["start"]), args.first_by))
    else:
        print("The first candidate starts at %s -- keep it unless it is pure setup, because "
              "a video that opens static reads as static." % fmt(candidates[0]["start"]))

    print("\nWrote %s" % args.output_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
