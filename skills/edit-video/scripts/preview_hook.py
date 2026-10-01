"""Show the chosen hook on the user's own video, before the long work starts.

    python preview_hook.py <raw video> --hook "<the hook they chose>" \\
        [--transcript <raw transcript.json>] [--template badge] [--open]

One still: a frame from the opening of their video, the hook placed exactly
where the final will put it (face-aware, clear of the captions), and their
chosen caption style running underneath -- by default twice, side by side and
numbered: 1 brand-colour box, 2 brand-colour text on a neutral box, for the
user to pick. It takes seconds.

Why it exists: after the questions the edit runs for minutes with nothing to
look at, and that is where people drift away. This is the first real result,
in their brand, on their face, right after they choose.

Writes to ~/.video-post/previews/<name>_hook.png and prints the path.
"""
import argparse
import io
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parents[2] / "lib"):
    if (_candidate / "caption_preview.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
try:
    import brand_profile as B
    import caption_preview as P
    import captions as C
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing a module (%s). Install with: python -m pip install pillow fonttools"
                     % exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="The RAW video, before the cut.")
    ap.add_argument("--hook", required=True)
    ap.add_argument("--transcript", help="Transcript of the raw video (from Step 1). Makes the "
                    "preview start where the speech starts and show their real words.")
    ap.add_argument("--template", choices=sorted(C.TEMPLATES),
                    help="Caption style; defaults to the saved one.")
    ap.add_argument("--language", default="en", choices=sorted(P.SAMPLE))
    ap.add_argument("--style", choices=("box", "text"),
                    help="Render one hook style only. Default: both, side by side and "
                         "numbered, for the user to choose from.")
    ap.add_argument("--open", action="store_true",
                    help="Also open the file in the computer's own viewer. Only when this "
                         "session cannot show files in the chat (a plain terminal).")
    ap.add_argument("--no-open", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    profile = B.load()
    if not profile:
        raise SystemExit("No brand saved. Ask for the font and colour, then run brand.py set.")
    template = args.template or profile.get("template")
    if not template:
        raise SystemExit("No caption template chosen yet. Show the previews and ask first.")
    fonts, problems = B.check(profile, extra_text=args.hook)
    if problems:
        for msg in problems:
            print("CANNOT PREVIEW: %s" % msg)
        return 2

    words, start = None, 0.0
    if args.transcript:
        words = [w for w in json.load(io.open(args.transcript, encoding="utf-8"))["words"]
                 if "start" in w and "end" in w]
        if words:
            # The cut opens on the first spoken word, so the preview does too.
            start = max(0.0, words[0]["start"] - 0.02)

    out = os.path.join(B.HOME, "previews")
    os.makedirs(out, exist_ok=True)
    if args.style:
        png = os.path.join(out, "%s_hook.png" % Path(args.video).stem)
        rep = P.render_hook_preview(args.video, args.hook, fonts, C.parse_hex(profile["colour"]),
                                    template, png, words=words, start=start,
                                    language=args.language, style=args.style)
    else:
        png = os.path.join(out, "%s_hook_styles.png" % Path(args.video).stem)
        rep = P.render_hook_styles(args.video, args.hook, fonts, C.parse_hex(profile["colour"]),
                                   template, png, words=words, start=start,
                                   language=args.language)
    print("Hook preview: %s" % png)
    print("  placed %s, %d line(s): %s" % (rep["where"], len(rep["lines"]), " / ".join(rep["lines"])))
    if rep["clash"]:
        print("  WARNING: no clear space; the hook overlaps the face in this framing.")
    if args.open and not args.no_open:
        P.open_file(png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
