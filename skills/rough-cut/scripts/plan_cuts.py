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


def _levels(media, rate=16000, hop_s=0.02):
    """Loudness of the recording in 20 ms steps, in dBFS (mono)."""
    import subprocess
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", media, "-vn", "-ac", "1", "-ar",
                          str(rate), "-f", "s16le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    hop = int(rate * hop_s)
    n = len(x) // hop
    if n == 0:
        return None, hop_s
    rms = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    return 20 * np.log10(rms + 1e-9), hop_s


def plan_gap_cuts(transcript, media, residual_s, lead_pad_s, trail_pad_s,
                  quiet_below_speech_db=15.0, word_pad_s=0.06, min_gap_s=0.25):
    """Cut the pauses that are not silent: breath, rustle, a turn to the script.

    WHY THIS EXISTS
    The silence cuts only take what is quieter than -45 dB. A real recording's
    pauses are often not that quiet: a breath, a chair, the rustle of turning to
    a laptop to read the next line of the script. On Danny's DJI test the video
    opened on 1.3 s of that (-35 to -55 dB) before the first word, and every
    look at the script left a second or more of head-turning in the cut --
    visible, because the picture shows exactly what the sound is doing.

    HOW
    A pause is measured against the recording's OWN speech level, not a fixed
    number: any stretch whose 20 ms level stays more than
    `quiet_below_speech_db` under the speech level (the 80th percentile) is not
    speech. Its edges come from the sound itself. Whisper's word times were
    tried first and are not precise enough at the start of a word: on the DJI
    test every word after a pause was stamped 0.3-0.45 s early ("Day" at 1.44 s,
    the voice at 1.74 s), so cutting to the transcript left most of each pause
    in place.

    The transcript is the safety net: a stretch that would swallow a whole word
    is left alone (a word Whisper heard there is speech, however soft), and the
    `word_pad_s` either side of every cut keeps the soft edge of a consonant.
    Each pause comes down to the two pads: 120 ms between phrases.
    """
    import numpy as np
    words = [w for w in transcript.get("words", []) if "start" in w and "end" in w]
    if not words or not media:
        return [], []
    db, hop = _levels(media)
    if db is None:
        return [], []
    speech = float(np.percentile(db, 80))
    limit = speech - quiet_below_speech_db
    duration = min(transcript["duration"], len(db) * hop)
    quiet = db < limit

    runs, i, n = [], 0, len(db)
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            runs.append((i * hop, min(j * hop, duration)))
            i = j
        else:
            i += 1

    def swallows_a_word(a, b):
        return any(w["start"] >= a + 0.02 and w["end"] <= b - 0.02 for w in words)

    cuts, handled = [], []
    first_start, last_end = words[0]["start"], words[-1]["end"]
    half = residual_s / 2.0
    for a, b in runs:
        if b - a < min_gap_s or swallows_a_word(a, b):
            continue
        if a <= hop and b < last_end:
            # before the first word: open right where the voice starts
            end = b - max(lead_pad_s, word_pad_s)
            if end > 0.02:
                cuts.append({"start": 0.0, "end": round(end, 3),
                             "reason": "leading silence (breath and noise before the first word)"})
                handled.append((0.0, b))
        elif b >= duration - hop and a > first_start:
            start = a + max(trail_pad_s, word_pad_s)
            if duration - start > 0.02:
                cuts.append({"start": round(start, 3), "end": round(duration, 3),
                             "reason": "trailing silence"})
                handled.append((a, duration))
        else:
            # The two word pads ARE the pause that is left: 120 ms of the room
            # between two phrases. A further 90 ms residual in the middle made
            # 210 ms, which the verifier rightly calls dead air (> 150 ms) when
            # the room is quiet.
            g0, g1 = a + word_pad_s, b - word_pad_s
            if g1 - g0 <= 0.02:
                continue
            cuts.append({"start": round(g0, 3), "end": round(g1, 3),
                         "reason": "silence (breath and noise between words)"})
            handled.append((a, b))
    return [c for c in cuts if c["end"] - c["start"] > 0.01], handled


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
    ap.add_argument("--media", help="The video or audio the transcript is of. Enables the "
                    "between-words cuts: breath, rustle and head-turns that are not quiet "
                    "enough for the silence cut. Always pass it.")
    args = ap.parse_args()

    with open(args.transcript_json, encoding="utf-8") as f:
        transcript = json.load(f)

    cuts = plan_silence_cuts(
        transcript,
        args.residual_ms / 1000.0,
        args.lead_pad_ms / 1000.0,
        args.trail_pad_ms / 1000.0,
    )
    if args.media:
        extra, handled = plan_gap_cuts(transcript, args.media, args.residual_ms / 1000.0,
                                       args.lead_pad_ms / 1000.0, args.trail_pad_ms / 1000.0)
        # Where a gap is cut this way, it replaces the silence cuts inside it.
        # Both together would each keep their 90 ms pause in a different spot,
        # and the union of the two cuts would leave no pause at all.
        def inside(c):
            return any(g0 - 0.001 <= c["start"] and c["end"] <= g1 + 0.001 for g0, g1 in handled)
        cuts = [c for c in cuts if not inside(c)] + extra
        print("Between-words cuts: %d gap(s) of breath and noise taken down to a 90 ms pause"
              % len([1 for g in handled]))

    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump({"cuts": cuts}, f, indent=2)

    removed = sum(c["end"] - c["start"] for c in cuts)
    duration = transcript["duration"]
    print(f"Planned {len(cuts)} silence cuts removing {removed:.1f}s "
          f"({100 * removed / duration:.0f}% of {duration:.1f}s runtime)")
    print(f"Wrote {args.output_json}")


if __name__ == "__main__":
    main()
