"""Brand-styled captions, written as ASS subtitles for ffmpeg to draw.

WHY ASS RATHER THAN A TIMELINE EFFECT
-------------------------------------
Styled text cannot travel through FCP7 XML. The adjustment-layer experiment
settled that: Resolve exported its own adjustment clip as a "Solid Color"
generator, and re-importing it put an opaque block on the timeline. So captions
are drawn into pixels here, where the brand font and colours are under our
control. ASS is what libass reads, and libass is what ffmpeg draws with.

THE FOUR TEMPLATES, EACH TAKEN FROM A REAL REFERENCE
----------------------------------------------------
  spotlight  one word at a time, large, uppercase, brand colour, no background
  badge      one word at a time on a tight rounded brand-colour chip
  impact     one to three words, uppercase, white, centre of frame
  emphasis   a short phrase in white with the key word set heavier

THREE THINGS THAT WENT WRONG THE FIRST TIME, AND WHY THE CODE IS SHAPED THIS WAY
------------------------------------------------------------------------------
1. Size was tied to one font. Sizes are now CAP HEIGHT as a share of the frame's
   short side, measured from the references. A brand font with tall capitals
   and one with short capitals come out the same visual size.

2. The badge chip was loose on every side. libass sizes text by LINE HEIGHT
   (ascender plus descender), while a font measured at the same number is
   sized by em. Every word was measured wider than it was drawn, and padding
   went on top of that. `_em_for` converts between the two, so the chip is
   built around the text libass actually draws.

3. The shadow read as a shadow. An offset copy of the text looks like a second
   word printed behind the first. It is now a separate, heavily blurred, dim
   layer: it lifts the text off a busy background without being visible as a
   thing in its own right.
"""
import os
import re
import shutil
import urllib.request

# --------------------------------------------------------------- brand colour

def parse_hex(colour, default="#FFFFFF"):
    c = (colour or default).strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        raise ValueError("colour %r is not a 6-digit hex value" % colour)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def ass_colour(rgb, alpha=0):
    """ASS wants &HAABBGGRR, and its alpha is inverted: 00 is fully opaque."""
    r, g, b = rgb
    return "&H%02X%02X%02X%02X" % (alpha, b, g, r)


def _tag_colour(rgb):
    """The same colour for an inline override tag, which takes &HBBGGRR&."""
    r, g, b = rgb
    return "&H%02X%02X%02X&" % (b, g, r)


def readable_on(background_rgb):
    """White or near-black, whichever reads on this colour.

    Every brand is not red. White text on a yellow or mint chip disappears, so
    the badge picks its text colour from the chip's luminance (WCAG formula).
    """
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = background_rgb
    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return (255, 255, 255) if lum < 0.45 else (17, 17, 17)


# ------------------------------------------------------------------- fonts

# 900 is included so a family whose heaviest weight is Black, not ExtraBold,
# still gets its heaviest weight rather than stopping at Bold.
WEIGHTS = (400, 500, 700, 800, 900)
CACHE = os.path.join(os.path.expanduser("~"), ".video-post", "fonts")


class FontSet(object):
    """One family in several weights, plus the folder libass should load from.

    `files` maps a CSS weight to a file. Templates ask for a weight and get the
    closest one available, so a brand font that only ships Regular and Bold
    still works: heavy templates fall back to Bold rather than failing.
    """

    def __init__(self, family, files, folder, note):
        self.family, self.files, self.folder, self.note = family, files, folder, note
        self.faces = {w: _face_name(p, family) for w, p in files.items()}

    def face_for(self, weight):
        """(family name to request, bold flag, PostScript name) for a weight.

        Asking libass for "Rethink Sans" at weight 800 returns Bold, not
        ExtraBold. A font file only answers to the family name written inside
        it, and by the standard convention only Regular and Bold carry the
        plain family name; ExtraBold calls itself "Rethink Sans ExtraBold",
        Medium "Rethink Sans Medium", and so on. The log showed every template
        rendering a step lighter than designed. So each weight is requested by
        the name its own file uses.
        """
        w = self.weight_for(weight)
        if w in self.faces:
            return self.faces[w]
        return (self.family, weight >= 600, None)

    def file_for(self, weight):
        if not self.files:
            return None
        best = min(self.files, key=lambda w: (abs(w - weight), -w))
        return self.files[best]

    def weight_for(self, weight):
        if not self.files:
            return weight
        return min(self.files, key=lambda w: (abs(w - weight), -w))


def fetch_google_font(family, cache=CACHE):
    """Download a Google Font as static TTF files, one per weight, and cache it.

    A plain request to the CSS API returns one full TTF per weight. A modern
    browser's user agent returns woff2 split into subsets instead, which libass
    cannot rely on, so no user agent is sent on purpose.
    """
    folder = os.path.join(cache, re.sub(r"[^A-Za-z0-9]+", "-", family).strip("-"))
    if os.path.isdir(folder):
        files = _scan_weights(folder)
        if files:
            return files, folder
    # One request per weight. Asking for several at once fails outright if the
    # family lacks any one of them: Lato has no 500 or 800, so a combined request
    # would refuse a perfectly good Google Font.
    css = ""
    for w in WEIGHTS:
        url = "https://fonts.googleapis.com/css2?family=%s:wght@%d" % (family.replace(" ", "+"), w)
        try:
            css += urllib.request.urlopen(url, timeout=20).read().decode("utf-8")
        except Exception:
            continue
    if not css:
        return {}, None
    # Only files from the Google Fonts LIBRARY (/s/<family>/) count. For some
    # commercial names -- tested with "Helvetica Neue" and "Proxima Nova" -- the
    # API answers 200 and serves generated stand-in files from /l/, labelled with
    # the name that was asked for. Accepting those would render the captions in
    # a substitute while every label claimed it was the brand font.
    found = re.findall(r"font-weight:\s*(\d+);.*?url\((https://fonts\.gstatic\.com/s/[^)]+\.ttf)\)",
                       css, re.S)
    if not found:
        return {}, None
    os.makedirs(folder, exist_ok=True)
    files = {}
    for weight, src in found:
        dst = os.path.join(folder, "%s-%s.ttf" % (os.path.basename(folder), weight))
        if not os.path.isfile(dst):
            with urllib.request.urlopen(src, timeout=30) as r, open(dst, "wb") as f:
                f.write(r.read())
        # Belt and braces: the file has to call itself by the requested name.
        # "Starts with", not "equals": plenty of fonts name their heavier
        # weights "Poppins ExtraBold" internally, and an exact match rejected
        # those, quietly dropping ExtraBold back to Bold. A stand-in such as a
        # different family entirely still fails this.
        inner = _typographic_family(dst)
        if inner and not _norm(inner).startswith(_norm(family)):
            os.remove(dst)
            continue
        files[int(weight)] = dst
    if not files:
        shutil.rmtree(folder, ignore_errors=True)
        return {}, None
    return files, folder


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _scan_weights(folder):
    files = {}
    for fn in os.listdir(folder):
        m = re.search(r"-(\d{3})\.(ttf|otf)$", fn)
        if m:
            files[int(m.group(1))] = os.path.join(folder, fn)
    return files


# Faces present on a clean machine, used only when the user gave no brand font
# and nothing could be fetched. Nothing is bundled, because fonts carry licences.
_SYSTEM = {
    "nt": ("Segoe UI", {400: r"C:\Windows\Fonts\segoeui.ttf",
                        700: r"C:\Windows\Fonts\segoeuib.ttf",
                        800: r"C:\Windows\Fonts\seguibl.ttf"}, r"C:\Windows\Fonts"),
    "posix": ("Helvetica", {400: "/System/Library/Fonts/Helvetica.ttc"},
              "/System/Library/Fonts"),
}


def resolve_font(family=None, font_files=None):
    """Work out which font to use, in the order a user would expect.

    1. Files they supplied: their brand, and the only way to use a licensed
       font without anyone shipping it.
    2. A Google Font named by family: fetched and cached once.
    3. A system fallback, with a note saying so, so nobody wonders why their
       captions are not in their font.
    """
    if font_files:
        files = {}
        for path in font_files:
            if not os.path.isfile(path):
                raise SystemExit("Font file not found: %s" % path)
            files[_guess_weight(path)] = os.path.abspath(path)
        fam = family or _family_of(next(iter(files.values())))
        folder = os.path.dirname(next(iter(files.values())))
        return FontSet(fam, files, folder, "using the font files you supplied")

    if family:
        files, folder = fetch_google_font(family)
        if files:
            return FontSet(family, files, folder,
                           "using %s from Google Fonts, %d weights" % (family, len(files)))
        files = _find_installed(family)
        if files:
            folder = os.path.dirname(next(iter(files.values())))
            return FontSet(family, files, folder,
                           "using %s, installed on this machine" % family)
        # A brand font was named and cannot be found. Substituting quietly is the
        # one thing that must never happen: the captions would look finished and
        # be wrong, and nobody would notice until they were posted.
        raise FontNotAvailable(family)

    fam, files, folder = _SYSTEM.get(os.name, _SYSTEM["posix"])
    files = {w: p for w, p in files.items() if os.path.isfile(p)}
    return FontSet(fam, files, folder, "no brand font given, so using %s" % fam)


class FontNotAvailable(Exception):
    """A named brand font is neither on Google Fonts nor installed here.

    Callers must stop and ask: for the font file, or for permission to use a
    named free alternative. Never fall back silently.
    """

    def __init__(self, family):
        Exception.__init__(self, family)
        self.family = family

    def __str__(self):
        return ("%s is not on Google Fonts and is not installed on this machine. It is "
                "probably a licensed font: ask for the font file, or propose the closest "
                "free alternative by name and wait for a yes." % self.family)


def _find_installed(family):
    """Weights of a family installed on this machine, keyed by weight."""
    want = family.lower().strip()
    dirs = [r"C:\Windows\Fonts",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
            os.path.expanduser("~/Library/Fonts"), "/Library/Fonts",
            "/System/Library/Fonts", "/System/Library/Fonts/Supplemental",
            "/usr/share/fonts", os.path.expanduser("~/.fonts"),
            os.path.expanduser("~/.local/share/fonts")]
    found = {}
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for root, _, names in os.walk(d):
            for fn in names:
                if not fn.lower().endswith((".ttf", ".otf")):
                    continue
                path = os.path.join(root, fn)
                fam = _typographic_family(path)
                if fam and fam.lower() == want:
                    found.setdefault(_guess_weight(path), path)
    return found


def _typographic_family(path):
    """The family a person would call it: name ID 16 if present, else ID 1."""
    try:
        from fontTools.ttLib import TTFont
        n = TTFont(path, lazy=True)["name"]
        return n.getDebugName(16) or n.getDebugName(1)
    except Exception:
        try:
            from PIL import ImageFont
            return ImageFont.truetype(path, 20).getname()[0]
        except Exception:
            return None


def _guess_weight(path):
    """Weight from a font filename.

    A number wins, then a style word -- matched as a whole word only. Matching
    substrings sounds harmless and is not: "thin" sits inside "RethinkSans", so
    every Rethink Sans file once registered as weight 100 and overwrote the
    others, leaving one face where there should have been four.
    """
    n = os.path.splitext(os.path.basename(path))[0].lower()
    m = re.search(r"(?<!\d)([1-9]00)(?!\d)", n)
    if m:
        return int(m.group(1))
    tokens = set(re.split(r"[-_ .]+", n))
    for key, w in (("extrabold", 800), ("ultrabold", 800), ("black", 900), ("heavy", 900),
                   ("semibold", 600), ("demibold", 600), ("bold", 700), ("medium", 500),
                   ("regular", 400), ("book", 400), ("light", 300), ("thin", 100)):
        if key in tokens:
            return w
    try:
        from PIL import ImageFont
        style = ImageFont.truetype(path, 20).getname()[1].lower().replace(" ", "")
        for key, w in (("extrabold", 800), ("black", 900), ("semibold", 600), ("bold", 700),
                       ("medium", 500), ("light", 300), ("thin", 100)):
            if key in style:
                return w
    except Exception:
        pass
    return 400


def _face_name(path, family):
    """The legacy family name (name ID 1) and bold bit a font file answers to.

    Read from the file when fontTools is present. Without it, fall back to the
    naming convention Google Fonts and most foundries follow: Regular and Bold
    share the family name, every other weight appends its style to it.
    """
    try:
        from fontTools.ttLib import TTFont
        t = TTFont(path, lazy=True)
        name = t["name"]
        fam = name.getDebugName(1) or family
        sub = (name.getDebugName(2) or "").lower()
        ps = name.getDebugName(6)
        return (fam, "bold" in sub, ps)
    except Exception:
        pass
    try:
        from PIL import ImageFont
        fam, style = ImageFont.truetype(path, 20).getname()
        s = style.lower().replace(" ", "")
        if s in ("regular", "bold", "italic", "bolditalic"):
            return (fam, "bold" in s, None)
        return ("%s %s" % (fam, style), False, None)
    except Exception:
        return (family, False, None)


def _family_of(path):
    try:
        from PIL import ImageFont
        return ImageFont.truetype(path, 20).getname()[0]
    except Exception:
        return os.path.splitext(os.path.basename(path))[0]


# ------------------------------------------------------------- measurement

def _pil(font_file, em):
    from PIL import ImageFont
    return ImageFont.truetype(font_file, max(1, int(round(em))))


def _em_for(font_file, ass_size):
    """The em libass actually draws when it is told `ass_size`.

    libass treats the size as line height, ascender to descender, so the em it
    renders is smaller than the number it was given by the ratio below.
    """
    if not font_file:
        return ass_size * 0.8
    f = _pil(font_file, 200)
    ascent, descent = f.getmetrics()
    return ass_size * 200.0 / float(ascent + descent)


def _ass_size_for_cap(font_file, cap_px):
    """ASS font size whose capital letters come out `cap_px` tall."""
    if not font_file:
        return cap_px / 0.56
    f = _pil(font_file, 200)
    box = f.getbbox("H")
    cap_at_200 = float(box[3] - box[1])
    ascent, descent = f.getmetrics()
    em = cap_px * 200.0 / cap_at_200
    return em * (ascent + descent) / 200.0


def text_width(text, font_file, ass_size, spacing_px=0):
    """Width of a string as libass will draw it."""
    if not font_file:
        return int(ass_size * 0.5 * len(text))
    f = _pil(font_file, _em_for(font_file, ass_size))
    box = f.getbbox(text)
    return (box[2] - box[0]) + spacing_px * max(0, len(text) - 1)


def cap_height(font_file, ass_size):
    if not font_file:
        return ass_size * 0.56
    f = _pil(font_file, _em_for(font_file, ass_size))
    box = f.getbbox("H")
    return box[3] - box[1]


# ---------------------------------------------------------------- profanity

_PROFANITY = ["fucking", "fuck", "shit", "bitch", "bastard", "asshole", "cunt",
              "dick", "piss", "scheisse", "scheiße", "fotze", "arschloch"]


def mask_profanity(word):
    """F*CKING, as the reference does it: first and last letter kept."""
    bare = re.sub(r"[^\w]", "", word, flags=re.UNICODE).lower()
    if bare not in _PROFANITY or len(bare) < 3:
        return word
    out, seen = [], 0
    for ch in word:
        if ch.isalpha():
            seen += 1
            out.append(ch if seen == 1 or seen == len(bare) else "*")
        else:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------- templates

# cap:      capital-letter height as a share of the frame's SHORT side, taken
#           from the references (vertical frames, so the short side is width)
# y:        centre of the caption as a share of height, vertical then horizontal.
#           Horizontal sits lower, because mid-frame on 16:9 is the chin.
# tracking: letter spacing as a share of cap height; negative is tighter
# Every template is ExtraBold (800). Lighter weights read as medium on video,
# however clean they look in a font preview; the feedback on the first round
# was "semi-bold at best". A font without 800 falls to its heaviest weight.
#
# Placement sits around the neck, below the mouth, in all four. The first
# version put Impact at mid-frame, which on a head-and-shoulders shot lands on
# the speaker's lips.
TEMPLATES = {
    # Tracking tightened on request, but not until letters touch: at -0.04 the
    # O and W of "HOW" met in a lossless close-up. -0.025 keeps it tight and clean.
    "spotlight": {"words": 1, "max_chars": 14, "case": "upper", "weight": 800, "cap": 0.060,
                  "y": (0.645, 0.78), "tracking": -0.025, "halo": True, "chip": False,
                  "emphasis": False, "colour": "brand",
                  "blurb": "one word at a time, large, in the brand colour"},
    "badge":     {"words": 1, "max_chars": 14, "case": "asis", "weight": 800, "cap": 0.038,
                  "y": (0.69, 0.80), "tracking": -0.02, "halo": False, "chip": True,
                  "emphasis": False, "colour": "text",
                  "blurb": "one word at a time on a tight rounded brand-colour chip"},
    "impact":    {"words": 3, "max_chars": 18, "case": "upper", "weight": 800, "cap": 0.050,
                  "y": (0.60, 0.76), "tracking": -0.02, "halo": True, "chip": False,
                  "emphasis": False, "colour": "text",
                  "blurb": "one to three words, uppercase, white, at the neck"},
    # With everything already ExtraBold there is no heavier weight left to set
    # the key word in, so it is marked in the brand colour instead.
    "emphasis":  {"words": 5, "max_chars": 26, "case": "asis", "weight": 800, "cap": 0.036,
                  "y": (0.575, 0.80), "tracking": -0.01, "halo": True, "chip": False,
                  "emphasis": True, "colour": "text",
                  "blurb": "a short phrase with the key word in the brand colour"},
}

STOPWORDS = set("""a an the and or but if of to in on at for with from by is are was were
be been am do does did not no so it its this that these those i you he she we they me him
her them my your his our their as than then there here what when where which who how why
will would can could should may might must have has had just very really about into over
und der die das ein eine ist sind war waren nicht auch noch schon aber oder wenn dann dass
ich du er sie wir ihr mit von den dem des zu für auf im in am""".split())


def pick_emphasis(words):
    """The word a speaker leans on: the longest one that carries meaning."""
    best, best_score = None, -1
    for i, w in enumerate(words):
        bare = re.sub(r"[^\w]", "", w, flags=re.UNICODE).lower()
        if not bare or bare in STOPWORDS:
            continue
        score = len(bare) + (1 if i >= len(words) - 2 else 0)
        if score > best_score:
            best, best_score = i, score
    return best


def group_words(words, per_group, max_chars, gap=0.34):
    """Break the transcript into caption events.

    Breaks on sentence punctuation, on any real pause, and before a line would
    get too long to sit on one row, so a caption never runs across the end of
    a thought just to fill its word count.
    """
    groups, current, chars = [], [], 0
    for i, w in enumerate(words):
        word = w.get("word", "").strip()
        if not word:
            continue
        if current and chars + 1 + len(word) > max_chars:
            groups.append(current)
            current, chars = [], 0
        current.append(w)
        chars += (1 if chars else 0) + len(word)
        ends = bool(re.search(r"[.!?,:;]$", word))
        nxt = (words[i + 1]["start"] - w["end"]) if i + 1 < len(words) else 99
        if len(current) >= per_group or ends or nxt >= gap:
            groups.append(current)
            current, chars = [], 0
    if current:
        groups.append(current)
    return groups


def _case(text, mode):
    return text.upper() if mode == "upper" else text


def _rounded_rect(w, h, r):
    """An ASS drawing for the badge chip. ASS has no corner radius of its own."""
    r = max(1, min(r, w / 2.0, h / 2.0))
    return ("m {r} 0 l {wr} 0 b {w} 0 {w} 0 {w} {r} l {w} {hr} b {w} {h} {w} {h} {wr} {h} "
            "l {r} {h} b 0 {h} 0 {h} 0 {hr} l 0 {r} b 0 0 0 0 {r} 0").format(
        r=int(round(r)), w=int(round(w)), h=int(round(h)),
        wr=int(round(w - r)), hr=int(round(h - r)))


def _ts(seconds):
    seconds = max(0.0, seconds)
    h, rem = int(seconds // 3600), seconds % 3600
    return "%d:%02d:%05.2f" % (h, int(rem // 60), rem % 60)


def build_ass(words, template, width, height, brand_rgb, text_rgb, fonts,
              mask=True, emphasis_index=None, orientation=None):
    """The whole caption track as one ASS document."""
    if template not in TEMPLATES:
        raise SystemExit("Unknown template %r. Choose from: %s"
                         % (template, ", ".join(sorted(TEMPLATES))))
    t = TEMPLATES[template]
    orientation = orientation or ("vertical" if height > width else "horizontal")
    short = min(width, height)
    ffile = fonts.file_for(t["weight"])
    face, face_bold, _ = fonts.face_for(t["weight"])

    size = _ass_size_for_cap(ffile, short * t["cap"])
    cap = cap_height(ffile, size)
    spacing = cap * t["tracking"]

    groups = group_words(words, t["words"], t["max_chars"])
    texts = [_case(" ".join(w["word"].strip() for w in g), t["case"]) for g in groups]
    # Never let the longest caption run off the frame; shrink the whole track
    # together rather than one caption on its own, so sizes never jump.
    widest = max([text_width(x, ffile, size, spacing) for x in texts] or [0])
    limit = width * 0.86
    if widest > limit:
        size *= limit / widest
        cap = cap_height(ffile, size)
        spacing = cap * t["tracking"]

    cx = width // 2
    cy = int(round(height * (t["y"][0] if orientation == "vertical" else t["y"][1])))
    if t["chip"]:
        text_rgb = readable_on(brand_rgb)
    fill = ass_colour(brand_rgb if t["colour"] == "brand" else text_rgb)

    head = [
        "[Script Info]", "ScriptType: v4.00+",
        "PlayResX: %d" % width, "PlayResY: %d" % height,
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
         "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
         "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"),
        # No outline and no offset shadow in the style itself, for any template.
        # Both read as amateur in the references; legibility comes from the halo.
        # The face is requested by the family name its own file carries, with
        # the bold bit exactly as that file declares it -- see FontSet.face_for.
        ("Style: Cap,%s,%d,%s,&H000000FF,&H00000000,&H00000000,%d,0,0,0,100,100,%.1f,0,1,0,0,5,0,0,0,1"
         % (face, int(round(size)), fill, -1 if face_bold else 0, spacing)),
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # The halo: a dim, heavily blurred copy behind the text. It darkens the
    # patch of picture under the words so they separate from a busy frame,
    # without showing up as a visible shadow shape.
    halo_tags = ("{\\bord%.1f\\blur%.1f\\shad0\\1c&H000000&\\3c&H000000&\\1a&HB0&\\3a&HB0&}"
                 % (cap * 0.22, cap * 0.55))

    events = []
    for gi, group in enumerate(groups):
        raw = [w["word"].strip() for w in group if w["word"].strip()]
        if not raw:
            continue
        if mask:
            raw = [mask_profanity(r) for r in raw]

        start, end = group[0]["start"], group[-1]["end"]
        if gi + 1 < len(groups):
            nxt = groups[gi + 1][0]["start"]
            if nxt - end < 0.45:
                end = nxt          # no blink between words in continuous speech
        end = max(end, start + 0.12)
        span = "%s,%s" % (_ts(start), _ts(end))

        plain = _case(" ".join(raw), t["case"])
        body = halo_body = plain
        if t["emphasis"]:
            idx = emphasis_index.get(gi) if emphasis_index else None
            if idx is None:
                idx = pick_emphasis(raw)
            parts = []
            for i, word in enumerate(raw):
                word = _case(word, t["case"])
                parts.append("{\\1c%s}%s{\\1c%s}" % (_tag_colour(brand_rgb), word,
                                                     _tag_colour(text_rgb))
                             if i == idx else word)
            body = " ".join(parts)
            # The halo stays plain: a colour tag there would override the
            # halo's own black and draw a blurred red smear behind the word.

        pos = "{\\an5\\pos(%d,%d)}" % (cx, cy)

        if t["chip"]:
            # Built around the text libass draws, measured from the references:
            # sides hug the word, top and bottom breathe a little more.
            tw = text_width(plain, ffile, size, spacing)
            pad_x, pad_y = cap * 0.42, cap * 0.46
            cw, ch = tw + pad_x * 2, cap + pad_y * 2
            x0, y0 = cx - cw / 2.0, cy - ch / 2.0
            events.append("Dialogue: 0,%s,Cap,,0,0,0,,{\\an7\\pos(%d,%d)\\p1\\bord0\\shad0\\1c%s}%s{\\p0}"
                          % (span, int(round(x0)), int(round(y0)), _tag_colour(brand_rgb),
                             _rounded_rect(cw, ch, cap * 0.34)))
            # libass centres the line box, not the capitals, so nudge the text
            # until its capitals sit in the middle of the chip.
            pos = "{\\an5\\pos(%d,%d)}" % (cx, int(round(cy + _cap_offset(ffile, size))))

        if t["halo"]:
            events.append("Dialogue: 0,%s,Cap,,0,0,0,,%s%s%s" % (span, pos, halo_tags, halo_body))
        events.append("Dialogue: 1,%s,Cap,,0,0,0,,%s%s" % (span, pos, body))

    return "\n".join(head + events) + "\n"


def missing_characters(text, font_file):
    """Characters the font cannot draw, which would show as blanks or boxes.

    Obeying the brand font is the rule, but a font that lacks "ä" or "ß" would
    print gaps in a German caption. Demo and trial fonts are the usual culprit:
    they install and look normal, then ship with half the alphabet missing.
    """
    if not font_file:
        return []
    try:
        from fontTools.ttLib import TTFont
        cmap = TTFont(font_file, lazy=True).getBestCmap() or {}
    except Exception:
        return []
    return sorted({ch for ch in text if not ch.isspace() and ord(ch) not in cmap})


def verify_font_log(log_text, fonts, weight):
    """Prove from ffmpeg's own log that the brand font was drawn, at the weight asked.

    libass reports every face it picks as "(requested) -> PostScriptName". The
    first version of this feature asked for ExtraBold and was handed Bold for
    every caption, and nothing noticed, because nothing checked. A substituted
    font looks finished; only the log tells the truth.

    Returns (ok, message).
    """
    picks = re.findall(r"fontselect:\s*\(([^,]+),\s*(\d+),\s*\d+\)\s*->\s*([^,\s]+)", log_text)
    if not picks:
        return False, "no font selection found in the log -- run ffmpeg with -v verbose"
    _, _, want_ps = fonts.face_for(weight)
    got = sorted({p[2] for p in picks})
    if want_ps:
        if want_ps in got:
            return True, "drew %s, as requested" % want_ps
        return False, ("asked for %s but ffmpeg drew %s. The brand font was not obeyed."
                       % (want_ps, ", ".join(got)))
    # Without fontTools there is no PostScript name to compare, so settle for
    # the family: anything outside it is a substitution.
    stem = re.sub(r"\s+", "", fonts.family).lower()
    wrong = [g for g in got if not g.lower().replace("-", "").startswith(stem)]
    if wrong:
        return False, "ffmpeg substituted %s for %s" % (", ".join(wrong), fonts.family)
    return True, "drew %s" % ", ".join(got)


def _cap_offset(font_file, ass_size):
    """How far libass's line-box centre sits from the centre of the capitals."""
    if not font_file:
        return 0
    f = _pil(font_file, _em_for(font_file, ass_size))
    ascent, descent = f.getmetrics()
    box = f.getbbox("H")
    cap_mid_from_top = box[1] + (box[3] - box[1]) / 2.0
    line_mid_from_top = (ascent + descent) / 2.0
    return line_mid_from_top - cap_mid_from_top
