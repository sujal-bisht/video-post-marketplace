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
