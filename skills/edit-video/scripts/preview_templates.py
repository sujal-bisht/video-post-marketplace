"""Render the four caption templates on the user's own video, in their brand, and open them.

    python preview_templates.py <video> [--language de]

Needs a saved brand (brand.py set). Nothing is transcribed: the words are a
sample sentence, so this takes seconds and can happen before any real work.

The files go to ~/.video-post/previews, not into the user's folder: they are
for choosing, not deliverables.
"""
import argparse
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
    ap.add_argument("video")
    ap.add_argument("--language", default="en", choices=sorted(P.SAMPLE),
                    help="Language of the sample sentence. Match the video.")
    ap.add_argument("--no-open", action="store_true", help="Write the files but do not open them.")
    args = ap.parse_args()

    profile = B.load()
    if not profile:
        raise SystemExit("No brand saved. Ask for the font and colour, then run brand.py set.")
    fonts, problems = B.check(profile)
    if problems:
        for msg in problems:
            print("CANNOT PREVIEW: %s" % msg)
        return 2

    out = os.path.join(B.HOME, "previews")
    os.makedirs(out, exist_ok=True)
    stem = Path(args.video).stem
    png = os.path.join(out, "%s_caption_styles.png" % stem)
    mp4 = os.path.join(out, "%s_caption_styles.mp4" % stem)
    P.render_previews(args.video, fonts, C.parse_hex(profile["colour"]), png, mp4,
                      language=args.language)

    print("Previews, in %s and %s:" % (fonts.family, profile["colour"]))
    print("  still:  %s" % png)
    print("  moving: %s" % mp4)
    print()
    for name in P.ORDER:
        print("  %-12s %s" % (P.LABELS[name], C.TEMPLATES[name]["blurb"]))
    if not args.no_open:
        P.open_file(png)
        P.open_file(mp4)
    return 0


if __name__ == "__main__":
    sys.exit(main())
