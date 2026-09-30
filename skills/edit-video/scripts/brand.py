"""Show, set or update the user's video brand.

    python brand.py show
    python brand.py set --font "Rethink Sans" --colour "#E90D41"
    python brand.py set --font "Acme Grotesk" --font-file A-Regular.otf --font-file A-ExtraBold.otf --colour "#1D4ED8"
    python brand.py template badge

`set` refuses rather than guesses. A named font that cannot be found, or one
that cannot draw the letters captions need, stops here with the reason, so the
question goes back to the user instead of the captions quietly coming out in
something else.

Exit codes: 0 ok, 2 needs the user (font missing or broken), 1 bad input.
"""
import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parents[2] / "lib"):
    if (_candidate / "brand_profile.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
try:
    import brand_profile as B
    import captions as C
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing a module (%s). Install with: python -m pip install pillow fonttools"
                     % exc)


def show():
    p = B.load()
    if not p:
        print("No brand saved yet.")
        return 0
    print(json.dumps(p, indent=2))
    fonts, problems = B.check(p)
    if fonts:
        print("\nfont: %s" % fonts.note)
    for msg in problems:
        print("PROBLEM: %s" % msg)
    return 2 if problems else 0


def set_brand(args):
    profile = dict(B.load() or {})
    if args.font is not None:
        profile["font"] = args.font
    if args.font_file:
        profile["font_files"] = [str(Path(f).resolve()) for f in args.font_file]
    elif args.font is not None:
        profile.pop("font_files", None)
    if args.colour is not None:
        profile["colour"] = args.colour
    if args.template is not None:
        profile["template"] = args.template

    fonts, problems = B.check(profile)
    if problems:
        for msg in problems:
            print("CANNOT USE THIS BRAND YET: %s" % msg)
        return 2
    # Downloaded Google Fonts are cached; remember their files so the next
    # session does not need the network to find them again.
    if not args.font_file and fonts.files:
        profile["font_files"] = sorted(fonts.files.values())
    B.save(profile)
    print("Saved. %s, colour %s%s." % (fonts.note, profile["colour"],
                                        ", template %s" % profile["template"]
                                        if profile.get("template") else ""))
    print("ExtraBold is drawn from: %s" % fonts.file_for(800))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    s = sub.add_parser("set")
    s.add_argument("--font", help="Family name, e.g. 'Rethink Sans'. Google Fonts are fetched.")
    s.add_argument("--font-file", action="append",
                   help="A font file the user supplied. Repeat for each weight.")
    s.add_argument("--colour", "--color", dest="colour", help="Brand colour, e.g. #E90D41")
    s.add_argument("--template", choices=sorted(C.TEMPLATES))
    t = sub.add_parser("template")
    t.add_argument("name", choices=sorted(C.TEMPLATES))
    args = ap.parse_args()

    if args.cmd == "show":
        return show()
    if args.cmd == "template":
        p = B.load()
        if not p:
            print("Set the font and colour first.")
            return 1
        p["template"] = args.name
        B.save(p)
        print("Caption template: %s -- %s" % (args.name, C.TEMPLATES[args.name]["blurb"]))
        return 0
    return set_brand(args)


if __name__ == "__main__":
    sys.exit(main())
