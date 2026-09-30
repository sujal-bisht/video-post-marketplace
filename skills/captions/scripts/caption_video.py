"""Put brand captions on a video that is already edited.

    python caption_video.py <video> <transcript.json> <output_dir> [--template badge]

For a video that was cut somewhere else. Nothing is trimmed and nothing zooms:
it gets captions and that is all. For raw footage, use the edit-video skill,
which cuts, zooms and captions in one go.

Writes <name>_captioned.mp4 into <output_dir>.
"""
import argparse
import io
import json
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parents[2] / "lib"):
    if (_candidate / "final_render.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
try:
    import brand_profile as B
    import captions as C
    import final_render as F
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing a module (%s). Install with: python -m pip install pillow fonttools"
                     % exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("transcript_json")
    ap.add_argument("output_dir")
    ap.add_argument("--template", choices=sorted(C.TEMPLATES))
    ap.add_argument("--no-mask", action="store_true")
    ap.add_argument("--full-resolution", action="store_true")
    args = ap.parse_args()

    profile = B.load()
    if not profile:
        raise SystemExit("No brand saved. Ask for the font and colour first "
                         "(edit-video/scripts/brand.py set).")
    template = args.template or profile.get("template")
    if not template:
        raise SystemExit("No caption template chosen. Show the previews and ask.")

    words = [w for w in json.load(io.open(args.transcript_json, encoding="utf-8"))["words"]
             if "start" in w and "end" in w]
    fonts, problems = B.check(profile, extra_text=" ".join(w["word"] for w in words))
    if problems:
        for msg in problems:
            print("CANNOT CAPTION YET: %s" % msg)
        return 2

    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                           "-show_entries", "stream=width,height", "-of", "csv=p=0",
                           os.path.abspath(args.video)], capture_output=True, text=True).stdout
    w, h = [int(x) for x in info.strip().split(",")[:2]]
    ass = C.build_ass(words, template, w, h, C.parse_hex(profile["colour"]),
                      (255, 255, 255), fonts, mask=not args.no_mask)
    os.makedirs(B.HOME, exist_ok=True)
    ass_path = os.path.join(B.HOME, "last_captions.ass")
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass)

    os.makedirs(args.output_dir, exist_ok=True)
    out = os.path.join(args.output_dir, "%s_captioned.mp4" % Path(args.video).stem)
    log, (ow, oh) = F.burn_captions(args.video, ass_path, fonts, out, args.full_resolution)
    ok, msg = C.verify_font_log(log, fonts, C.TEMPLATES[template]["weight"])
    print("%s template, %s, %s" % (template, fonts.note, profile["colour"]))
    print("Wrote %s at %dx%d" % (out, ow, oh))
    print("font check: %s  %s" % ("PASS" if ok else "FAIL", msg))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
