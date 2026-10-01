"""Show the four caption templates on the user's own footage before they choose.

A generic example answers "what does this template look like?". The user's
actual question is "what will MY video look like?", and that needs their frame,
their font and their colour. So the preview is built from a still of the video
they are about to edit, in the brand they have just given, and it costs a few
seconds because nothing is transcribed yet: the words are a sample sentence.

Two files come out:
  <name>_caption_styles.png   a 2x2 still, to compare the four side by side
  <name>_caption_styles.mp4   the same grid moving, because the templates differ
                              most in rhythm: one word at a time versus a phrase
"""
import json
import os
import shutil
import subprocess
import tempfile

import captions as C

ORDER = ("spotlight", "badge", "impact", "emphasis")
LABELS = {"spotlight": "1  Spotlight", "badge": "2  Badge",
          "impact": "3  Impact", "emphasis": "4  Emphasis"}
SAMPLE = {
    "en": "This is how your captions will look on every single video you post",
    "de": "So sehen deine Untertitel in jedem einzelnen Video aus das du postest",
}


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height:format=duration",
                          "-of", "json", path], capture_output=True, text=True).stdout
    d = json.loads(out)
    s = d["streams"][0]
    return int(s["width"]), int(s["height"]), float(d["format"]["duration"])


def _sample_words(sentence, start=0.35, step=0.42):
    words, t = [], start
    for w in sentence.split():
        words.append({"word": w, "start": t, "end": t + step - 0.04})
        t += step
    return words


def render_previews(video, fonts, brand_rgb, out_png, out_mp4, language="en",
                    text_rgb=(255, 255, 255), seconds=6.0):
    """Build both previews. Returns the list of templates in grid order."""
    vw, vh, dur = _probe(video)
    # Previews are for looking at, not for delivery: short side 1080 is plenty
    # and keeps a 4K phone clip from turning this into a slow step.
    scale = 1080.0 / min(vw, vh)
    pw, ph = int(round(vw * scale / 2)) * 2, int(round(vh * scale / 2)) * 2

    work = tempfile.mkdtemp(prefix="vp-preview-")
    try:
        os.makedirs(os.path.join(work, "fonts"))
        for p in fonts.files.values():
            shutil.copy(p, os.path.join(work, "fonts"))

        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "%.2f" % (dur * 0.4),
                        "-i", os.path.abspath(video), "-frames:v", "1",
                        "-vf", "scale=%d:%d" % (pw, ph), "still.png"], cwd=work, check=True)

        words = _sample_words(SAMPLE.get(language, SAMPLE["en"]))
        panels = []
        for tpl in ORDER:
            ass = C.build_ass(words, tpl, pw, ph, brand_rgb, text_rgb, fonts)
            ass += _label_event(tpl, pw, ph, fonts, seconds)
            with open(os.path.join(work, tpl + ".ass"), "w", encoding="utf-8") as f:
                f.write(ass)
            panel = tpl + ".mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "30",
                            "-i", "still.png", "-t", "%.2f" % seconds,
                            "-vf", "subtitles=%s.ass:fontsdir=fonts,scale=%d:%d"
                                   % (tpl, pw // 2, ph // 2),
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                            "-pix_fmt", "yuv420p", panel], cwd=work, check=True)
            panels.append(panel)

        cmd = ["ffmpeg", "-y", "-v", "error"]
        for p in panels:
            cmd += ["-i", p]
        cmd += ["-filter_complex", "[0][1][2][3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", os.path.abspath(out_mp4)]
        subprocess.run(cmd, cwd=work, check=True)

        # The still is drawn straight out of libass into PNG and stacked there,
        # never through a video codec. Taken from the compressed grid, saturated
        # brand colours smeared at the edges -- a red ghost beside the letters --
        # and the preview made the captions look worse than the real output,
        # which is the one thing a preview must not do.
        stills = []
        for tpl in ORDER:
            png = tpl + ".png"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", "still.png",
                            "-t", "3", "-vf", "subtitles=%s.ass:fontsdir=fonts,scale=%d:%d:flags=lanczos"
                                                % (tpl, pw // 2, ph // 2),
                            "-ss", "1.6", "-frames:v", "1", png], cwd=work, check=True)
            stills.append(png)
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for p in stills:
            cmd += ["-i", p]
        cmd += ["-filter_complex", "[0][1][2][3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0",
                os.path.abspath(out_png)]
        subprocess.run(cmd, cwd=work, check=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return list(ORDER)


def _label_event(template, w, h, fonts, seconds):
    """The template's number and name across the top of its panel."""
    face, bold, _ = fonts.face_for(800)
    size = int(round(min(w, h) * 0.075))
    return ("Dialogue: 2,0:00:00.00,%s,Cap,,0,0,0,,{\\an8\\pos(%d,%d)\\fn%s\\b%d\\fs%d"
            "\\1c&HFFFFFF&\\bord%.0f\\3c&H000000&\\3a&H60&\\blur%.0f\\fsp0}%s\n"
            % (C._ts(seconds), w // 2, int(h * 0.06), face, 1 if bold else 0, size,
               size * 0.12, size * 0.3, LABELS[template]))


def open_file(path):
    """Put the preview in front of the user, on whatever machine this is."""
    try:
        if os.name == "nt":
            os.startfile(path)
        elif shutil.which("open"):
            subprocess.Popen(["open", path])
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def render_hook_preview(video, hook, fonts, brand_rgb, template, out_png, words=None,
                        start=0.0, language="en", style="box"):
    """One still of the hook on the user's own video, placed exactly as the
    final will place it, with their caption style running underneath.

    Shown right after the hook is chosen, before the long work starts: the
    first real result of the edit, in seconds. Returns the placement report.

    `words` are transcript words of the RAW video; the preview looks at the
    4.5 seconds from `start` (normally the first spoken word), because that is
    what the opening of the cut will be. Without words a sample sentence is
    used for the caption.
    """
    import context_line as H
    vw, vh, dur = _probe(video)
    scale = 1080.0 / min(vw, vh)
    pw, ph = int(round(vw * scale / 2)) * 2, int(round(vh * scale / 2)) * 2
    start = max(0.0, min(start, max(0.0, dur - 1.0)))
    end = min(dur, start + H.HOOK_SECONDS)

    events, report = H.plan(hook, video, pw, ph, fonts, brand_rgb, caption_template=template,
                            start=start, end=end, draw_span=(0.0, end - start), style=style)
    if words:
        spoken = [dict(w, start=w["start"] - start, end=w["end"] - start) for w in words
                  if "start" in w and "end" in w and start <= w["start"] < end]
    else:
        spoken = []
    if not spoken:
        spoken = _sample_words(SAMPLE.get(language, SAMPLE["en"]))
    # Show the moment a caption is on screen, as close to 1.5s in as possible.
    at = 1.5
    on = [w for w in spoken if w["start"] <= at <= w["end"] + 0.3]
    if not on:
        at = min(spoken, key=lambda w: abs(w["start"] - 1.5))["start"] + 0.05

    work = tempfile.mkdtemp(prefix="vp-hookpreview-")
    try:
        os.makedirs(os.path.join(work, "fonts"))
        for p in fonts.files.values():
            shutil.copy(p, os.path.join(work, "fonts"))
        ass = C.build_ass(spoken, template, pw, ph, brand_rgb, (255, 255, 255), fonts)
        with open(os.path.join(work, "hook.ass"), "w", encoding="utf-8") as f:
            f.write(ass.rstrip("\n") + "\n" + "\n".join(events) + "\n")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", os.path.abspath(video),
                        "-ss", "%.3f" % (start + at), "-frames:v", "1",
                        "-vf", "scale=%d:%d" % (pw, ph), "still.png"], cwd=work, check=True)
        # Drawn straight to PNG, never through a video codec -- see render_previews.
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", "still.png",
                        "-t", "%.2f" % (at + 1.0),
                        "-vf", "subtitles=hook.ass:fontsdir=fonts,scale=%d:%d:flags=lanczos"
                               % (pw * 2 // 3 // 2 * 2, ph * 2 // 3 // 2 * 2),
                        "-ss", "%.3f" % at, "-frames:v", "1", os.path.abspath(out_png)],
                       cwd=work, check=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return report


def render_hook_styles(video, hook, fonts, brand_rgb, template, out_png, words=None,
                       start=0.0, language="en"):
    """Both hook styles on the user's video, side by side and numbered, in one PNG.

    The hook question is answered by looking, like the caption style: the same
    opening frame twice, once as a brand-colour box with neutral text, once as
    brand-colour text on a neutral box. Returns the placement report.
    """
    import context_line as H
    work = tempfile.mkdtemp(prefix="vp-hookstyles-")
    try:
        panels, report = [], None
        for i, style in enumerate(H.HOOK_STYLES, start=1):
            panel = os.path.join(work, "%s.png" % style)
            report = render_hook_preview(video, hook, fonts, brand_rgb, template, panel,
                                         words=words, start=start, language=language,
                                         style=style)
            labelled = os.path.join(work, "%s_l.png" % style)
            _label_png(panel, labelled, "%d  %s" % (i, H.HOOK_STYLE_LABELS[style]), fonts)
            panels.append(labelled)
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for p in panels:
            cmd += ["-i", p]
        cmd += ["-filter_complex", "[0][1]hstack=inputs=2", os.path.abspath(out_png)]
        subprocess.run(cmd, check=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return report


def _label_png(src, dst, label, fonts):
    """Write a number and name along the bottom of a preview panel."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.open(src).convert("RGB")
    w, h = img.size
    band = int(h * 0.07)
    out = Image.new("RGB", (w, h + band), (17, 17, 17))
    out.paste(img, (0, 0))
    d = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype(fonts.file_for(800), int(band * 0.5))
    except Exception:
        font = ImageFont.load_default()
    tw = d.textlength(label, font=font)
    d.text(((w - tw) / 2, h + band * 0.22), label, fill=(255, 255, 255), font=font)
    out.save(dst)
