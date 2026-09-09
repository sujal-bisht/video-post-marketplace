"""
Plan silence cuts deterministically from measured audio silence.

WHY THIS SCRIPT EXISTS
----------------------
Silence removal used to be left to reasoning over a transcript, and it failed
badly in practice: only 16s of dead air got removed from a clip that contained
109s of it. Two causes, both now designed out:

  1. Silence was inferred from gaps between word timestamps. Whisper hides
     silence inside inflated word durations, so most dead air was invisible.
     Fixed by measuring actual audio level (transcribe.py --> audio_silences).
  2. Deciding cuts by hand is error-prone and easy to under-apply.
     Fixed by making it arithmetic: every measured silence gets compressed,
     with no judgment involved.

Deciding *whether a sentence is a restart* still needs language understanding
and stays with the model. Deciding *where dead air is* does not. This script
owns the second job so it can never silently under-cut again.

Every internal silence is compressed to `--residual-ms` (not deleted outright),
which keeps a micro-beat so speech doesn't sound machine-gunned, while staying
under the "no audible dead air" bar. Leading silence is removed right up to the
first word; trailing silence down to a short tail.

Usage:
    python plan_cuts.py <transcript.json> <cutlist_silence.json>
        [--residual-ms 60] [--lead-pad-ms 20] [--trail-pad-ms 120]
"""
import json
import argparse


def plan_silence_cuts(transcript, residual_s, lead_pad_s, trail_pad_s):
    duration = transcript["duration"]
    silences = transcript.get("audio_silences")
    if silences is None:
        raise SystemExit(
            "transcript.json has no 'audio_silences'. Re-run transcribe.py -- an older "
            "transcript that only has word gaps will massively under-detect dead air."
        )

    cuts = []
    for s in silences:
        start, end, dur = s["start"], s["end"], s["duration"]
        position = s.get("position", "mid")

        if position == "leading":
            # Start the video essentially on the first spoken word.
            cut_end = max(0.0, end - lead_pad_s)
            if cut_end > 0:
                cuts.append({"start": 0.0, "end": round(cut_end, 3),
                             "reason": "leading silence"})
            continue

        if position == "trailing":
            cut_start = min(duration, start + trail_pad_s)
            if duration - cut_start > 0.02:
                cuts.append({"start": round(cut_start, 3), "end": round(duration, 3),
                             "reason": "trailing silence"})
            continue

        # Internal dead air: compress to `residual_s`, split evenly so the pause
        # shrinks from both sides and neither adjacent word gets clipped.
        if dur <= residual_s:
            continue
        half = residual_s / 2.0
        cut_start = start + half
        cut_end = end - half
        if cut_end > cut_start:
            cuts.append({"start": round(cut_start, 3), "end": round(cut_end, 3),
                         "reason": "silence"})

    return cuts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript_json")
    ap.add_argument("output_json")
    ap.add_argument("--residual-ms", type=float, default=90,
                    help="How much of each internal pause to keep, in ms. 90 leaves ~45ms of "
                         "guard on each side of a cut, which matters because quiet consonants "
                         "sit right at the silence boundary -- too tight and you shave the 's' "
                         "off a word. Still under the audible-dead-air threshold. Raise to "
                         "150-250 if the result feels rushed or choppy.")
    ap.add_argument("--lead-pad-ms", type=float, default=20,
                    help="Silence left before the first word, in ms.")
    ap.add_argument("--trail-pad-ms", type=float, default=120,
                    help="Silence left after the last word, in ms.")
    args = ap.parse_args()

    with open(args.transcript_json, encoding="utf-8") as f:
        transcript = json.load(f)

    cuts = plan_silence_cuts(
        transcript,
        args.residual_ms / 1000.0,
        args.lead_pad_ms / 1000.0,
        args.trail_pad_ms / 1000.0,
    )

    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump({"cuts": cuts}, f, indent=2)

    removed = sum(c["end"] - c["start"] for c in cuts)
    duration = transcript["duration"]
    print(f"Planned {len(cuts)} silence cuts removing {removed:.1f}s "
          f"({100 * removed / duration:.0f}% of {duration:.1f}s runtime)")
    print(f"Wrote {args.output_json}")


if __name__ == "__main__":
    main()
