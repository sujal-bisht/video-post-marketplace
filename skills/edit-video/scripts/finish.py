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
    import music as M
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing a module (%s). Install with: python -m pip install pillow fonttools"
                     % exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline_xml")
    ap.add_argument("--video", help="Not needed: the picture is read from whatever the "
                    "timeline's V1 plays (the linked original, or a trimmed file). Kept so "
                    "older commands still run.")
    ap.add_argument("--zooms", help="zooms.json chosen from prepare.py's candidates. Applied "
                    "to the timeline and verified before rendering, so the whole finish is one "
                    "command.")
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
    ap.add_argument("--music-mood", choices=M.MOODS,
                    help="The video's feel, judged from the transcript: picks the background track.")
    ap.add_argument("--music", help="A specific track instead of picking by mood.")
    ap.add_argument("--no-music", action="store_true",
                    help="Only when the user asked for no background music.")
    ap.add_argument("--hook-horizontal", action="store_true",
                    help="Allow the hook on a horizontal video. Only when the user asked.")
    args = ap.parse_args()

    xml = os.path.abspath(args.timeline_xml)
    out_dir = os.path.dirname(xml)
    name = Path(xml).stem
    if args.zooms:
        _run_zoom_step("apply_zooms.py", [xml, os.path.abspath(args.zooms)])
        _run_zoom_step("verify_zooms.py", [xml])
    fps, w, h, dur = F.timeline_shape(xml)
    media_path, segs, _ = F.picture_source(xml)
    print("Timeline %s: %dx%d, %.2f fps, %.1fs, playing %s"
          % (name, w, h, fps, dur, os.path.basename(media_path)))

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
            hook_events, rep = H.plan(args.hook, media_path, w, h, fonts,
                                      C.parse_hex(profile["colour"]), caption_template=template,
                                      zoom_at=lambda t: _zoom_at(pts, t), start=0.0, end=end,
                                      time_map=lambda t: F.timeline_to_source(segs, t))
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

    # The editor layers (captions alone, hook alone) are rendered AT THE SAME
    # TIME as the final, not after it. They are mostly empty frames, cheap on
    # the processor but slow end to end: on a 4-minute 4K timeline the caption
    # layer alone took 6 minutes, waiting in line behind the final.
    media_dir = os.path.join(out_dir, "%s_media" % name)
    layers = []
    if ass_path and not args.no_caption_track:
        layers.append(("captions", ass_path, os.path.join(media_dir, "%s_captions.mov" % name),
                       0.0, dur))
    if hook_ass and not args.no_caption_track:
        end = min(H.HOOK_SECONDS, dur)
        layers.append(("hook", hook_ass, os.path.join(media_dir, "%s_hook.mov" % name), 0.0, end))
    if layers:
        os.makedirs(media_dir, exist_ok=True)
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=max(1, len(layers)))
    t0 = time.time()
    jobs = [(kind, mov, a, b, pool.submit(F.render_caption_layer, script, fonts, mov, w, h, fps,
                                          b - a))
            for kind, script, mov, a, b in layers]

    music_wav = None
    if not args.no_music:
        music_wav = _music_bed(args, xml, out_dir, name, fps, dur)

    final = os.path.join(out_dir, "%s_final.mp4" % name)
    log = F.render_final(args.video or media_path, xml, final, ass_path=burn_ass, fonts=fonts,
                         full_resolution=args.full_resolution, music_wav=music_wav)
    ow, oh = F.output_size(w, h, args.full_resolution)
    print("Rendered %s at %dx%d in %.0fs" % (os.path.basename(final), ow, oh, time.time() - t0))

    ok = True
    if burn_ass:
        good, msg = C.verify_font_log(log, fonts, C.TEMPLATES[template]["weight"] if template
                                      else 800)
        print("  font check: %s  %s" % ("PASS" if good else "FAIL", msg))
        ok &= good

    for kind, mov, a, b, job in jobs:
        job.result()
        if kind == "captions":
            F.add_caption_track(xml, mov, w, h)
        else:
            F.add_layer_track(xml, mov, w, h, kind, a, b)
        print("%s track added to the timeline (%s); all layers done %.0fs after the start"
              % (kind.capitalize(), os.path.relpath(mov, out_dir), time.time() - t0))
    pool.shutdown()

    print()
    print("Post this one:  %s" % final)
    if not ok:
        print("\nThe brand font was NOT what ffmpeg drew. Do not hand this over.")
        return 1
    return 0


def _music_bed(args, xml, out_dir, name, fps, dur):
    """Pick the track, build the ducked bed, put it on the timeline. Returns the wav."""
    import music_library as L
    if args.music:
        known = [t for t in M.library(L.folders(), quiet=True)
                 if os.path.abspath(t["path"]) == os.path.abspath(args.music)]
        track = known[0] if known else dict(M.analyze(args.music), path=os.path.abspath(args.music),
                                            title=Path(args.music).stem)
    else:
        if not args.music_mood:
            raise SystemExit("Background music needs --music-mood (the video's feel, from the "
                             "transcript), a specific --music track, or --no-music.")
        track = M.pick(args.music_mood, dur, L.folders())
        if not track:
            print("Music skipped: the library is empty (no starter tracks, no music folder).")
            return None
    words = []
    if args.transcript:
        words = [w for w in json.load(io.open(args.transcript, encoding="utf-8"))["words"]
                 if "start" in w and "end" in w]
    media_dir = os.path.join(out_dir, "%s_media" % name)
    os.makedirs(media_dir, exist_ok=True)
    bed = os.path.join(media_dir, "%s_music.wav" % name)
    rep = M.build_bed(track, dur, words, bed)
    tb = int(round(fps))
    ntsc = "TRUE" if abs(fps - round(fps)) > 0.01 else "FALSE"
    M.add_music_tracks(xml, bed, tb, ntsc, int(round(dur * fps)))
    M.remember(track["path"])
    print("Music: %s (%s)%s, %.0f LUFS in the gaps, %.0f under speech; on its own timeline tracks"
          % (rep["track"], rep["mood"], ", looped %d time(s)" % rep["loops"] if rep["loops"] else "",
             rep["level_lufs_unducked"], rep["ducked_lufs"]))
    return bed


def _run_zoom_step(script, argv):
    """Run one slow-zoom script and stop the finish if it fails."""
    import subprocess
    here = Path(__file__).resolve().parent
    for cand in (here.parents[1] / "slow-zoom" / "scripts" / script, here / script):
        if cand.is_file():
            r = subprocess.run([sys.executable, str(cand)] + argv)
            if r.returncode != 0:
                raise SystemExit("%s failed -- fix the zoom plan before rendering." % script)
            return
    raise SystemExit("Cannot find the slow-zoom skill's %s." % script)


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
