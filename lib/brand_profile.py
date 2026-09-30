"""The user's brand for video: font, colour, and chosen caption template.

Asked for at the start of a session, saved here, and offered back next time so
nobody retypes it for every video. It is still shown and confirmed at the start
of each session -- the brand is the user's to change, and a stale saved answer
applied silently is how captions end up in last month's colours.
"""
import datetime
import json
import os

import captions as C

HOME = os.path.join(os.path.expanduser("~"), ".video-post")
PATH = os.path.join(HOME, "brand.json")

# The sample the font is checked against before it is accepted. It carries the
# letters a demo or trial font most often lacks, German ones included, because
# a font that cannot draw them prints gaps in the captions.
COVERAGE_SAMPLE = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz 0123456789 "
                   ".,!?'\"-:;()*& ÄÖÜäöüß éèêàçñ")


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save(profile):
    os.makedirs(HOME, exist_ok=True)
    profile = dict(profile, updated=datetime.date.today().isoformat())
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
    return profile


def fonts_for(profile):
    """Resolve the profile's font into a FontSet. Raises C.FontNotAvailable."""
    files = [p for p in (profile.get("font_files") or []) if os.path.isfile(p)]
    fonts = C.resolve_font(family=profile.get("font"), font_files=files or None)
    # Files fetched from Google Fonts are remembered in the profile, so they come
    # back as "files" -- say where they really came from.
    if files and all(os.path.abspath(p).startswith(os.path.abspath(C.CACHE)) for p in files):
        fonts.note = "using %s from Google Fonts (saved on this machine)" % fonts.family
    return fonts


def check(profile, extra_text=""):
    """Everything that must be true before the brand is used. Returns (fonts, problems)."""
    problems = []
    try:
        C.parse_hex(profile.get("colour"))
    except Exception:
        problems.append("the brand colour %r is not a hex value like #E90D41"
                        % profile.get("colour"))
    try:
        fonts = fonts_for(profile)
    except C.FontNotAvailable as e:
        return None, problems + [str(e)]
    missing = C.missing_characters(COVERAGE_SAMPLE + " " + extra_text, fonts.file_for(800))
    if missing:
        problems.append("%s cannot draw these characters: %s. Captions would show gaps "
                        "where they appear. This is usually a demo or trial copy of the "
                        "font: ask for the full version, or a free alternative by name."
                        % (fonts.family, " ".join(missing)))
    if profile.get("template") and profile["template"] not in C.TEMPLATES:
        problems.append("unknown caption template %r" % profile["template"])
    return fonts, problems
