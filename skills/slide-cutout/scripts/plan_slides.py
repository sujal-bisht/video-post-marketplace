"""
Turn spoken cue phrases into slide time ranges.

WHY CUE PHRASES
---------------
Asking someone to supply timestamps for 100 videos is asking them to do the
tedious work by hand -- exactly what this is meant to remove. But they already
know what they said. "Slide 2 starts when I say 'now the second piece'" is
something a person can write from memory, and a word-level transcript turns it
into an exact time.

MATCHING HAS TO BE FORGIVING, AND HONEST
----------------------------------------
Transcription will not reproduce a remembered phrase character for character:
"let's" vs "lets", a dropped "the", a mis-heard name. So matching is fuzzy.

But fuzzy matching that silently picks the closest thing is worse than no
matching -- a slide lands in the wrong place and nobody knows why. So every
result reports how it was found, a weak match is refused rather than guessed,
and a phrase that occurs twice is flagged instead of resolved by coin flip.

Input plan (written from what the user says, they never format this themselves):
{
  "slides": [
    {"image": "slide-01.png", "cue": "the first framework"},
    {"image": "slide-02.png", "cue": "now the second piece"}
  ],
  "end_cue": "so that is the theory"        // optional: back to full frame here
}

Each slide shows from its cue until the next cue, or `end_cue`, or the end of
the video.

Usage:
    python plan_slides.py <transcript.json> <plan.json> <resolved.json>
        [--slides-dir DIR] [--min-score 0.6]
"""
import argparse
import difflib
import json
import os
import re
import sys

# Unicode-aware: an a-z class splits "glueckliche" at the umlaut into two
# tokens. Both the cue and the transcript get mangled the same way so
# matching still lands, but token counts then stop matching word counts,
# which makes scores and reported positions harder to trust. Real footage
# here is German, so accented letters are the norm, not an edge case.
WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def normalise(text):
    return WORD_RE.findall(text.lower())


def match_cue(cue, words, min_score):
    """Find where a cue phrase is spoken.

    Returns (start_seconds, report_dict). Slides a window the length of the cue
    across the transcript and scores each position, rather than looking for an
    exact substring, so small transcription differences do not break the match.
    """
    tokens = [(normalise(w["word"]), w) for w in words]
    flat = []
    for norm_list, w in tokens:
        for t in norm_list:
            flat.append((t, w))

    cue_tokens = normalise(cue)
    if not cue_tokens:
        return None, {"status": "empty", "cue": cue}
    if not flat:
        return None, {"status": "no_transcript", "cue": cue}

    n = len(cue_tokens)
    scored = []
    for i in range(0, max(1, len(flat) - n + 1)):
        window = [t for t, _ in flat[i:i + n]]
        if not window:
            continue
        score = difflib.SequenceMatcher(None, cue_tokens, window).ratio()
        scored.append((score, i))

    scored.sort(key=lambda x: (-x[0], x[1]))
    best_score, best_i = scored[0]
    start = flat[best_i][1]["start"]

    if best_score < min_score:
        # Refuse rather than guess, and show what was actually nearby so the user
        # can correct the phrase instead of wondering why a slide vanished.
        suggestions = []
        for score, i in scored[:3]:
            text = " ".join(t for t, _ in flat[i:i + n])
            suggestions.append({"score": round(score, 2),
                                "at": round(flat[i][1]["start"], 2),
                                "heard": text})
        return None, {"status": "not_found", "cue": cue,
                      "best_score": round(best_score, 2), "nearest": suggestions}

    # A repeated phrase is ambiguous: a rival match that scores nearly as well and
    # sits somewhere else entirely means the cue does not identify one moment.
    rivals = [(s, i) for s, i in scored[1:]
              if s >= best_score - 0.05 and abs(flat[i][1]["start"] - start) > 3.0]
    report = {"status": "matched", "cue": cue, "score": round(best_score, 2),
              "at": round(start, 2),
              "heard": " ".join(t for t, _ in flat[best_i:best_i + n])}
    if rivals:
        report["status"] = "ambiguous"
        report["also_at"] = [round(flat[i][1]["start"], 2) for _, i in rivals[:3]]
    return start, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript_json")
    ap.add_argument("plan_json")
    ap.add_argument("output_json")
    ap.add_argument("--slides-dir", default=None,
                    help="Folder holding the slide images named in the plan.")
    ap.add_argument("--min-score", type=float, default=0.6,
                    help="Below this the cue is treated as not found rather than guessed.")
    args = ap.parse_args()

    with open(args.transcript_json, encoding="utf-8") as f:
        transcript = json.load(f)
    with open(args.plan_json, encoding="utf-8") as f:
        plan = json.load(f)

    words = transcript["words"]
    duration = transcript["duration"]
    slides = plan.get("slides", [])
    if not slides:
        raise SystemExit("The plan lists no slides.")

    resolved, problems = [], []
    for entry in slides:
        image = entry["image"]
        if args.slides_dir:
            image_path = os.path.join(args.slides_dir, image)
        else:
            image_path = image
        if not os.path.isfile(image_path):
            problems.append({"status": "missing_image", "image": image_path})
            continue

        start, report = match_cue(entry["cue"], words, args.min_score)
        report["image"] = os.path.abspath(image_path)
        if start is None:
            problems.append(report)
            continue
        resolved.append({"image": os.path.abspath(image_path),
                         "start": start, "cue": entry["cue"], "report": report})

    resolved.sort(key=lambda r: r["start"])

    end_time = duration
    if plan.get("end_cue"):
        end_start, end_report = match_cue(plan["end_cue"], words, args.min_score)
        if end_start is None:
            problems.append(end_report)
        else:
            end_time = end_start

    # Each slide runs until the next one begins, and the last until end_cue or
    # the end of the video.
    for i, slide in enumerate(resolved):
        slide["end"] = resolved[i + 1]["start"] if i + 1 < len(resolved) else end_time
        if slide["end"] <= slide["start"]:
            problems.append({"status": "zero_length", "image": slide["image"],
                             "start": slide["start"], "end": slide["end"]})

    resolved = [s for s in resolved if s["end"] > s["start"]]

    out = {"duration": duration, "slides": resolved, "problems": problems}
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    def fmt(t):
        return "%d:%05.2f" % (int(t // 60), t % 60)

    print("Resolved %d of %d slides" % (len(resolved), len(slides)))
    for s in resolved:
        r = s["report"]
        flag = "" if r["status"] == "matched" else "  [%s]" % r["status"].upper()
        print("  %-28s %s -> %s  (match %.2f)%s"
              % (os.path.basename(s["image"]), fmt(s["start"]), fmt(s["end"]),
                 r["score"], flag))
        if r["status"] == "ambiguous":
            print("      cue also spoken at: %s -- reword it to be unique"
                  % ", ".join(fmt(t) for t in r["also_at"]))

    if problems:
        print("\n%d problem(s) -- these slides will NOT appear:" % len(problems))
        for p in problems:
            if p["status"] == "not_found":
                print("  cue not found: %r (best %.2f)" % (p["cue"], p["best_score"]))
                for s in p["nearest"]:
                    print("      at %s the transcript reads: %r (%.2f)"
                          % (fmt(s["at"]), s["heard"], s["score"]))
            elif p["status"] == "missing_image":
                print("  image missing: %s" % p["image"])
            else:
                print("  %s: %s" % (p["status"], p.get("image", p.get("cue", ""))))

    print("\nWrote %s" % args.output_json)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
