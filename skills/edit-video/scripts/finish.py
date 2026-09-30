"""Finish an edit: captions, and one post-ready video with everything baked in.

    python finish.py <output_dir>/<name>.xml --video <output_dir>/<name>_trimmed.mp4 \\
        --transcript <scratch>/trimmed_transcript.json [--no-captions] [--template badge]

Run LAST, after the rough cut and the zooms (and slides, when used). It reads the
finished timeline and produces:

  <name>_final.mp4                 cut, zoomed, slides and cutout, captions on top.
                                   The file to post. Needs no editing software.
  <name>_media/<name>_captions.mov the captions alone, transparent, added to the
                                   timeline on their own top track

The timeline itself is only gaining a caption track. Everything else in it is
left exactly as the earlier steps wrote it.
"""
import argparse
import io
import json
import os
import sys
import time
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
    ap.add_argument("timeline_xml")
    ap.add_argument("--video", required=True, help="The media V1 plays: <name>_trimmed.mp4")
    ap.add_argument("--transcript", help="Transcript of that same video, for the captions.")
    ap.add_argument("--no-captions", action="store_true",
                    help="Only when the user asked for no captions.")
    ap.add_argument("--template", choices=sorted(C.TEMPLATES),
                    help="Override the saved template for this video only.")
    ap.add_argument("--no-caption-track", action="store_true",
                    help="Skip the transparent caption layer in the timeline.")
    ap.add_argument("--no-mask", action="store_true", help="Leave profanity unmasked.")
    ap.add_argument("--full-resolution", action="store_true",
                    help="Render the final at source size instead of 1080 on the short side. "
                         "Short-form platforms re-compress anything larger, so only use this "
                         "when the user needs a full-size file, e.g. for YouTube in 4K.")
    args = ap.parse_args()

    xml = os.path.abspath(args.timeline_xml)
    out_dir = os.path.dirname(xml)
    name = Path(xml).stem
    fps, w, h, dur = F.timeline_shape(xml)
    print("Timeline %s: %dx%d, %.2f fps, %.1fs" % (name, w, h, fps, dur))

    ass_path, fonts, template = None, None, None
    if not args.no_captions:
        if not args.transcript:
            raise SystemExit("Captions need --transcript (the trimmed video's transcript). "
                             "Pass --no-captions only if the user asked for none.")
        profile = B.load()
        if not profile:
            raise SystemExit("No brand saved. Ask for the font and colour first (brand.py set).")
        template = args.template or profile.get("template")
        if not template:
            raise SystemExit("No caption template chosen. Show the previews and ask.")
        words = [w_ for w_ in json.load(io.open(args.transcript, encoding="utf-8"))["words"]
                 if "start" in w_ and "end" in w_]
        spoken = " ".join(x["word"] for x in words)
        fonts, problems = B.check(profile, extra_text=spoken)
        if problems:
            for msg in problems:
                print("CANNOT CAPTION YET: %s" % msg)
            return 2
        ass = C.build_ass(words, template, w, h, C.parse_hex(profile["colour"]),
                          (255, 255, 255), fonts, mask=not args.no_mask)
        media = os.path.join(out_dir, "%s_media" % name)
        os.makedirs(media, exist_ok=True)
        ass_path = os.path.join(B.HOME, "last_captions.ass")
        os.makedirs(B.HOME, exist_ok=True)
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass)
        print("Captions: %s template, %s, %s" % (template, fonts.note, profile["colour"]))

    final = os.path.join(out_dir, "%s_final.mp4" % name)
    t0 = time.time()
    log = F.render_final(args.video, xml, final, ass_path=ass_path, fonts=fonts,
                         full_resolution=args.full_resolution)
    ow, oh = F.output_size(w, h, args.full_resolution)
    print("Rendered %s at %dx%d in %.0fs" % (os.path.basename(final), ow, oh, time.time() - t0))

    ok = True
    if ass_path:
        good, msg = C.verify_font_log(log, fonts, C.TEMPLATES[template]["weight"])
        print("  font check: %s  %s" % ("PASS" if good else "FAIL", msg))
        ok &= good

        if not args.no_caption_track:
            mov = os.path.join(out_dir, "%s_media" % name, "%s_captions.mov" % name)
            t1 = time.time()
            F.render_caption_layer(ass_path, fonts, mov, w, h, fps, dur)
            F.add_caption_track(xml, mov, w, h)
            print("Caption track added to the timeline in %.0fs (%s)"
                  % (time.time() - t1, os.path.relpath(mov, out_dir)))

    print()
    print("Post this one:  %s" % final)
    if not ok:
        print("\nThe brand font was NOT what ffmpeg drew. Do not hand this over.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
