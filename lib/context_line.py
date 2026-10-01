"""The context line: the hook text that sits on screen for the first seconds.

The speaker opens with their spoken hook; the context line tells the viewer, in
writing, what the video is about and that it is for them. This module only
draws and places it. WHAT it says is decided from the transcript, against the
standards in edit-video/references/hook-standards.md.

THE LOOK, FROM THE REFERENCES
-----------------------------
The Instagram text-background style: every line gets its own rounded box in the
brand colour, the boxes merge into one shape, and the outline steps in and out
with the length of each line. Text in whichever of white or near-black reads
on the brand colour. On for the first 4.5 seconds, no animation in or out.

WHERE IT GOES
-------------
Wherever the face is not. Every reference moved the box to the empty part of
the frame: above the head when the head sits low, below the chin when it sits
high, between faces on a call grid. So placement is measured, not fixed:

  1. find the speaker's face across the seconds the hook is on screen, because
     people move, and add headroom for hair, which face detectors leave out
  2. allow for the zoom: if a push-in runs during the hook, the face gets bigger
     and moves toward the centre, and the box has to clear THAT face
  3. keep clear of the captions, which stay on screen under the hook
  4. stay inside the area the platform's own buttons do not cover
  5. prefer above the head; otherwise the biggest free band that fits
"""
import captions as C

HOOK_SECONDS = 4.5
# SIZE: the text grows until the widest box fills ~88% of the frame width, in
# at most three lines -- between these two capital heights (share of the short
# side). The first version used a fixed 0.042 with generous padding, and on a
# real video the box covered half the frame width with empty space on every
# side; next to the reference hooks (Hormozi-style boxes spanning the frame)
# it looked small and timid.
CAP_MAX, CAP_MIN = 0.078, 0.046
CAP = CAP_MIN
MAX_WIDTH = 0.88     # widest a box may get, as a share of frame width
MAX_LINES = 3
# Padding as a share of the capital height: tight, like the references --
# the box hugs the words.
PAD_X, PAD_Y, PITCH, RADIUS = 0.36, 0.30, 1.40, 0.30

# Instagram and TikTok cover the top ~11% and the bottom ~24% of a vertical
# video with their own interface. Text there is hidden behind it.
SAFE_TOP, SAFE_BOTTOM = 0.11, 0.76


# --------------------------------------------------------------- line breaks

def balanced_lines(words, width_of, max_width):
    """Split into as few lines as fit, then as evenly as possible.

    Never leaves one word stranded on the last line when another split exists:
    a lone "posts." under two full lines -- as in one of the references -- reads
    as if the line ran out of room rather than as a sentence.
    """
    n = len(words)
    if n == 0:
        return []
    # A line should end where a thought ends, so a break after a full stop or a
    # question mark wins over a tidier shape. Widths alone gave "Deine KI-Texte
    # klingen wie ein / Roboter. So änderst du das." -- even lines, broken
    # sense. And when the fewest lines cannot break at the sentence, one more
    # line is worth it: "... klingen / wie ein Roboter. / So änderst du das."
    sentences = sum(1 for w in words[:-1] if w[-1:] in ".?!")
    fallback = None
    for lines in range(1, min(n, 5) + 1):
        best, best_key = None, None
        for split in _splits(n, lines):
            rows = [" ".join(words[a:b]) for a, b in split]
            widths = [width_of(r) for r in rows]
            if max(widths) > max_width:
                continue
            orphan = lines > 1 and (split[-1][1] - split[-1][0]) == 1
            ends = sum(1 for a, b in split[:-1] if words[b - 1][-1:] in ".?!")
            pauses = sum(1 for a, b in split[:-1] if words[b - 1][-1:] in ":;,")
            key = (orphan, -ends, -pauses, max(widths) - min(widths), max(widths))
            if best_key is None or key < best_key:
                best, best_key = rows, key
        if not best:
            continue
        if not sentences or lines == 1 or -best_key[1] > 0 or fallback:
            # one extra line at most, and only to reach a sentence break
            return best if (not fallback or -best_key[1] > 0) else fallback
        fallback = best
    return fallback or [" ".join(words)]


def _splits(n, k):
    if k == 1:
        yield [(0, n)]
        return
    for first in range(1, n - k + 2):
        for rest in _splits(n - first, k - 1):
            yield [(0, first)] + [(a + first, b + first) for a, b in rest]


# ------------------------------------------------------------------ geometry

class Layout(object):
    """Everything needed to draw the hook once a vertical position is chosen."""

    def __init__(self, lines, size, cap, widths, pitch, pad_x, pad_y, radius):
        self.lines, self.size, self.cap, self.widths = lines, size, cap, widths
        self.pitch, self.pad_x, self.pad_y, self.radius = pitch, pad_x, pad_y, radius

    @property
    def height(self):
        return self.pitch * len(self.lines) + self.pad_y * 2


def layout(text, width, height, fonts, scale=1.0, max_lines=None):
    """The biggest hook that fits: largest text in at most three lines whose box
    stays within MAX_WIDTH of the frame. `scale` shrinks it when placement needs
    room."""
    short = min(width, height)
    ffile = fonts.file_for(800)
    words = text.split()
    best = None
    steps = 16
    for k in range(steps + 1):
        share = (CAP_MAX - (CAP_MAX - CAP_MIN) * k / steps) * scale
        size = C._ass_size_for_cap(ffile, short * share)
        cap = C.cap_height(ffile, size)
        spacing = cap * -0.01
        pad_x, pad_y = cap * PAD_X, cap * PAD_Y
        max_text = width * MAX_WIDTH - pad_x * 2
        width_of = lambda s, size=size, spacing=spacing: C.text_width(s, ffile, size, spacing)
        lines = balanced_lines(words, width_of, max_text)
        widths = [width_of(l) for l in lines]
        best = Layout(lines, size, cap, widths, cap * PITCH, pad_x, pad_y, cap * RADIUS)
        if len(lines) <= (max_lines or MAX_LINES) and max(widths) <= max_text:
            return best
    return best


# ----------------------------------------------------------------- placement

def _zoomed(box, z, width, height):
    """Where a face box lands once the frame is pushed in to z percent."""
    if abs(z - 100.0) < 0.01:
        return tuple(box[:4])
    f, cx, cy = z / 100.0, width / 2.0, height / 2.0
    x1, y1, x2, y2 = box[:4]
    return (cx + (x1 - cx) * f, cy + (y1 - cy) * f, cx + (x2 - cx) * f, cy + (y2 - cy) * f)


HAIR_FULL, HAIR_MEASURED, HAIR_MIN = 0.40, 0.30, 0.12


def occupied_bands(faces_by_time, zoom_at, width, height, hair=HAIR_FULL):
    """Vertical spans the faces use during the hook, hair and zoom included.

    `hair` is the headroom kept above the detected face, as a share of the
    face's height. Detectors box forehead to chin; measured on real footage the
    hair reached 0.30 of a face height above that. 0.40 clears it fully. When
    nothing fits, placement retries at 0.12 -- allowed to cover the top of the
    hair, never the face itself.
    """
    spans = []
    for t, boxes in faces_by_time.items():
        if not boxes:
            continue
        biggest = max((b[2] - b[0]) * (b[3] - b[1]) for b in boxes)
        for b in boxes:
            # Small faces in the background do not matter; anything at least
            # 40% of the speaker's size is another person on screen.
            if (b[2] - b[0]) * (b[3] - b[1]) < 0.4 * biggest:
                continue
            x1, y1, x2, y2 = _zoomed(b, zoom_at(t), width, height)
            fh = y2 - y1
            spans.append((y1 - fh * hair, y2 + fh * 0.12))
    return _merge(spans)


def _merge(spans):
    out = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def choose_y(lay, occupied, height, caption_band=None, only_above=False):
    """Centre line for the hook box, plus a note on why it went there."""
    gap = height * 0.015
    taken = list(occupied)
    if caption_band:
        taken.append(caption_band)
    taken = _merge(taken)
    top, bottom = height * SAFE_TOP, height * SAFE_BOTTOM

    free, cursor = [], top
    for s, e in taken:
        if s - gap > cursor:
            free.append((cursor, min(s - gap, bottom)))
        cursor = max(cursor, e + gap)
    if cursor < bottom:
        free.append((cursor, bottom))
    free = [(a, b) for a, b in free if b > a]

    fits = [(a, b) for a, b in free if b - a >= lay.height]
    if not fits:
        return None, "no free band tall enough"
    face_top = occupied[0][0] if occupied else None
    above = [(a, b) for a, b in fits if face_top is not None and b <= face_top]
    if above:
        a, b = above[0]
        return (a + b) / 2.0, "above the head"
    if only_above:
        return None, "no room above the head"
    a, b = max(fits, key=lambda ab: ab[1] - ab[0])
    where = "below the face" if face_top is not None and a >= face_top else "in the clearest band"
    return (a + b) / 2.0, where


# ------------------------------------------------------------------- drawing

def events(lay, width, cy, fonts, brand_rgb, start=0.0, end=HOOK_SECONDS, layer=5):
    """ASS events for the boxes and the text, positioned on centre line cy."""
    face, bold, _ = fonts.face_for(800)
    text_rgb = C.readable_on(brand_rgb)
    cx = width / 2.0
    # Lines whose widths nearly match are evened out, so the shape has a clean
    # straight side instead of a one-pixel ledge.
    widths = list(lay.widths)
    for i in range(1, len(widths)):
        if abs(widths[i] - widths[i - 1]) < lay.radius * 2:
            widths[i] = widths[i - 1] = max(widths[i], widths[i - 1])

    span = "%s,%s" % (C._ts(start), C._ts(end))
    top = cy - lay.height / 2.0
    out = []
    for i, (line, w) in enumerate(zip(lay.lines, widths)):
        line_top = top + lay.pad_y + lay.pitch * i
        y0 = line_top - (lay.pad_y if i == 0 else lay.radius)
        y1 = line_top + lay.pitch + (lay.pad_y if i == len(lay.lines) - 1 else lay.radius)
        bw = w + lay.pad_x * 2
        out.append("Dialogue: %d,%s,Cap,,0,0,0,,{\\an7\\pos(%d,%d)\\p1\\bord0\\shad0\\blur0\\1c%s\\1a&H00&}%s{\\p0}"
                   % (layer, span, int(round(cx - bw / 2.0)), int(round(y0)),
                      C._tag_colour(brand_rgb), C._rounded_rect(bw, y1 - y0, lay.radius)))
    for i, line in enumerate(lay.lines):
        mid = top + lay.pad_y + lay.pitch * (i + 0.5)
        mid += C._cap_offset(fonts.file_for(800), lay.size)
        out.append("Dialogue: %d,%s,Cap,,0,0,0,,{\\an5\\pos(%d,%d)\\fn%s\\b%d\\fs%d\\fsp%.1f"
                   "\\bord0\\shad0\\blur0\\1c%s\\1a&H00&}%s"
                   % (layer + 1, span, int(round(cx)), int(round(mid)), face, 1 if bold else 0,
                      int(round(lay.size)), lay.cap * -0.01, C._tag_colour(text_rgb), line))
    return out


def standalone_ass(width, height, event_lines):
    """A complete ASS file holding only the hook, for its own timeline track."""
    head = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: %d" % width, "PlayResY: %d" % height,
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
         "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
         "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"),
        "Style: Cap,Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    return "\n".join(head + list(event_lines)) + "\n"


def plan(text, video, width, height, fonts, brand_rgb, caption_template=None,
         zoom_at=lambda t: 100.0, start=0.0, end=HOOK_SECONDS, draw_span=None, time_map=None):
    """Lay out and place the hook. Returns (event_lines, report dict).

    Faces are looked for between `start` and `end` of `video`. The events are
    timed over that same span unless `draw_span` (start, end) says otherwise --
    a preview looks at the raw video's opening but draws onto a clip from 0.

    Faces are found on a small copy of each frame, which is all the detector
    needs, then scaled back to timeline coordinates.
    """
    import faces as F
    probe_w = 540
    probe_h = int(round(height * probe_w / float(width) / 2)) * 2
    seen = F.faces_over(video, start, end, samples=6, width=probe_w, height=probe_h,
                        time_map=time_map)
    s = width / float(probe_w)
    seen = {t: [(b[0] * s, b[1] * s, b[2] * s, b[3] * s, b[4]) for b in boxes]
            for t, boxes in seen.items()}
    band = caption_band(caption_template, width, height)

    # In order of preference:
    #   above the head, clear of the hair: full size (generous, then measured
    #   headroom), then a touch smaller
    #   above the head, full size, over the very top of the hair
    #   anywhere clear, full size -- usually below the chin
    #   anywhere, full size, over the top of the hair
    #   then a little smaller, a step at a time
    # Above wins when it fits: it is where every reference put it -- several
    # sit right on the crown -- while below the chin the hook stacks onto the
    # captions and the two read as one muddled block. The face itself is never covered
    # while any of these fits. Smaller text in the right place beats big text
    # across someone's mouth.
    attempts = [(HAIR_FULL, 1.0, True), (HAIR_MEASURED, 1.0, True), (HAIR_MEASURED, 0.92, True),
                (HAIR_MIN, 1.0, True),
                (HAIR_FULL, 1.0, False), (HAIR_MIN, 1.0, False),
                (HAIR_MIN, 0.92, False), (HAIR_MIN, 0.85, False), (HAIR_MIN, 0.78, False)]
    # At every step, the hook in three lines first (biggest text), then in two
    # wider lines: shorter overall, so it fits above a head the three-line box
    # does not -- better than shrinking it.
    for hair, scale, only_above in attempts:
        occupied = occupied_bands(seen, zoom_at, width, height, hair)
        for ml in (MAX_LINES, 2):
            lay = layout(text, width, height, fonts, scale, max_lines=ml)
            cy, where = choose_y(lay, occupied, height, band, only_above)
            if cy is not None:
                break
        if cy is not None:
            if hair < HAIR_MEASURED:
                where += ", over the top of the hair"
            break
    clash = cy is None
    if clash:
        # Nowhere clear: take the top of the safe area and say so.
        cy, where = height * SAFE_TOP + lay.height / 2.0, "top of the safe area, overlapping"
    report = {"lines": lay.lines, "y_share": round(cy / height, 3), "where": where,
              "scale": scale, "faces_found": sum(1 for b in seen.values() if b),
              "frames_checked": len(seen), "clash": clash}
    s0, s1 = draw_span or (start, end)
    return events(lay, width, cy, fonts, brand_rgb, s0, s1), report


def caption_band(template, width, height):
    """The vertical span the captions occupy, so the hook can keep clear of it."""
    if not template:
        return None
    t = C.TEMPLATES[template]
    short = min(width, height)
    cap = short * t["cap"]
    cy = height * (t["y"][0] if height > width else t["y"][1])
    half = cap * (1.35 if t["chip"] else 1.1)
    return (cy - half, cy + half)
