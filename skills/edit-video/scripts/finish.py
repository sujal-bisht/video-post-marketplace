"""Finish an edit: captions, and one post-ready video with everything baked in.

    python finish.py <output_dir>/<name>.xml --video <output_dir>/<name>_trimmed.mp4 \\
        --transcript <scratch>/trimmed_transcript.json [--no-captions] [--template badge]

Run LAST, after the rough cut and the zooms (and slides, when used). It reads the
finished timeline and produces:

  <name>_final.mp4                 cut, zoomed, slides and cutout, captions on top.
                                   The file to post. Needs no editing software.
  <name>_media/<name>_captions.mov the captions alone, transparent, added to the
                                   timeline on their own top track
  <name>_media/<name>_hook.mov     the hook (context line) alone, 4.5s, on its own
                                   track above that -- vertical videos only

The timeline itself is only gaining a caption track and a hook track. Everything else in it is
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
    import context_line as H
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
    ap.add_argument("--hook", help="The context line shown for the first 4.5s. Written from "
                    "the transcript against references/hook-standards.md and approved by the "
                    "user before the edit started.")
    ap.add_argument("--hook-horizontal", action="store_true",
                    help="Allow the hook on a horizontal video. Only when the user asked.")
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

    hook_ass = None
    if args.hook:
        if w > h and not args.hook_horizontal:
            print("Hook skipped: horizontal video. The hook is for short-form; pass "
                  "--hook-horizontal only if the user asked for it here.")
        else:
            profile = B.load()
            if not profile:
                raise SystemExit("No brand saved. The hook is drawn in the brand colour.")
            hook_fonts, problems = B.check(profile, extra_text=args.hook)
            fonts = fonts or hook_fonts
            if problems:
                for msg in problems:
                    print("CANNOT DRAW THE HOOK YET: %s" % msg)
                return 2
            pts, _ = F.zoom_curve(xml)
            end = min(H.HOOK_SECONDS, dur)
            hook_events, rep = H.plan(args.hook, args.video, w, h, fonts,
                                      C.parse_hex(profile["colour"]), caption_template=template,
                                      zoom_at=lambda t: _zoom_at(pts, t), start=0.0, end=end)
            print("Hook: %s, at %.0f%% of the height, %d line(s): %s"
                  % (rep["where"], rep["y_share"] * 100, len(rep["lines"]),
                     " / ".join(rep["lines"])))
            if rep["clash"]:
                print("  WARNING: no clear space found; the hook overlaps the face. Say so "
                      "in the handover.")
            os.makedirs(B.HOME, exist_ok=True)
            hook_ass = os.path.join(B.HOME, "last_hook.ass")
            with io.open(hook_ass, "w", encoding="utf-8") as f:
                f.write(H.standalone_ass(w, h, hook_events))
    # The final is burned in one pass from one script: captions plus hook. The
    # caption script itself stays captions-only, for the caption track.
    burn_ass = ass_path or hook_ass
    if ass_path and hook_ass:
        burn_ass = os.path.join(B.HOME, "last_final.ass")
        with io.open(burn_ass, "w", encoding="utf-8") as f:
            f.write(io.open(ass_path, encoding="utf-8").read().rstrip("\n") + "\n"
                    + "\n".join(hook_events) + "\n")

    final = os.path.join(out_dir, "%s_final.mp4" % name)
    t0 = time.time()
    log = F.render_final(args.video, xml, final, ass_path=burn_ass, fonts=fonts,
                         full_resolution=args.full_resolution)
    ow, oh = F.output_size(w, h, args.full_resolution)
    print("Rendered %s at %dx%d in %.0fs" % (os.path.basename(final), ow, oh, time.time() - t0))

    ok = True
    if burn_ass:
        good, msg = C.verify_font_log(log, fonts, C.TEMPLATES[template]["weight"] if template
                                      else 800)
        print("  font check: %s  %s" % ("PASS" if good else "FAIL", msg))
        ok &= good

        if ass_path and not args.no_caption_track:
            mov = os.path.join(out_dir, "%s_media" % name, "%s_captions.mov" % name)
            t1 = time.time()
            F.render_caption_layer(ass_path, fonts, mov, w, h, fps, dur)
            F.add_caption_track(xml, mov, w, h)
            print("Caption track added to the timeline in %.0fs (%s)"
                  % (time.time() - t1, os.path.relpath(mov, out_dir)))

    if hook_ass and not args.no_caption_track:
        media = os.path.join(out_dir, "%s_media" % name)
        os.makedirs(media, exist_ok=True)
        mov = os.path.join(media, "%s_hook.mov" % name)
        end = min(H.HOOK_SECONDS, dur)
        F.render_caption_layer(hook_ass, fonts, mov, w, h, fps, end)
        F.add_layer_track(xml, mov, w, h, "hook", 0.0, end)
        print("Hook track added to the timeline (%s)" % os.path.relpath(mov, out_dir))

    print()
    print("Post this one:  %s" % final)
    if not ok:
        print("\nThe brand font was NOT what ffmpeg drew. Do not hand this over.")
        return 1
    return 0


def _zoom_at(pts, t):
    """Zoom percent at time t, read off the timeline's own keyframes."""
    if not pts or t <= pts[0][0]:
        return pts[0][1] if pts else 100.0
    for (t1, v1), (t2, v2) in zip(pts, pts[1:]):
        if t1 <= t <= t2:
            return v1 if t2 <= t1 else v1 + (v2 - v1) * (t - t1) / (t2 - t1)
    return pts[-1][1]


if __name__ == "__main__":
    sys.exit(main())
