"""Everything between the decisions and the final render, in one command.

    python prepare.py <raw video> --transcript <scratch>/<name>_transcript.json \\
        --speech-cuts <scratch>/cutlist_speech.json --out <output dir> --scratch <scratch> \\
        [--name <name>] [--slides]

Runs, in order, the machine steps of the edit:

  1. plans the silence cuts from the measured audio
  2. builds the cut: the timeline and the cut audio, with the ORIGINAL footage
     linked into the output folder -- no video is re-encoded here
  3. transcribes the cut audio (that transcript drives the zooms and captions)
  4. verifies the cut: dead air, the timeline's sync, leftover restarts
  5. polishes the sound: rumble cut, noise reduced only if the room needs it,
     gentle compression, -14 LUFS (lib/audio_polish.py)
  6. lays out the zoom candidates (skipped with --slides: zooms go after slides)

and prints what the model has to decide next: any leftover repeats to judge,
and the zoom candidates to choose from. Then finish.py --zooms renders.

WHY ONE COMMAND
A 4-minute video once took 28 minutes, and a third of that was not the
computer working: it was 68 separate steps where nine would have done, each
one a round trip. The judgments still belong to the model -- which words are a
restart, which moments deserve a zoom -- but the plumbing between them does not.

WHY NO RE-ENCODE
The old cut rendered a full-resolution trimmed copy before anything else could
happen. On a 6-minute 4K phone video that one step took longer than every other
step put together, and it cost picture quality. The final render now reads the
original directly, cut on the fly, and the editor timeline plays the original
too. With --slides the trimmed copy is still made, because the speaker cutout
is rendered from it.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _script(skill, name):
    for cand in (HERE.parents[1] / skill / "scripts" / name, HERE / name):
        if cand.is_file():
            return str(cand)
    raise SystemExit("Cannot find the %s skill's %s. Reinstall the video-post plugin."
                     % (skill, name))


def _run(label, argv, show=True, ok_codes=(0,)):
    t = time.time()
    r = subprocess.run([sys.executable] + argv, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    took = time.time() - t
    print("== %s (%.0fs)" % (label, took))
    out = (r.stdout or "").strip()
    if show and out:
        print(out)
    if r.returncode not in ok_codes:
        err = (r.stderr or "").strip().splitlines()[-12:]
        print("\n".join(err))
        raise SystemExit("%s failed (exit %d). Fix that before going on." % (label, r.returncode))
    print()
    return r


def _polish(video, raw_transcript, wav):
    """Polish the cut audio in place; the timeline and the final both play it."""
    sys.path.insert(0, str(HERE.parents[2] / "lib"))
    sys.path.insert(0, str(HERE))
    import audio_polish as A
    t = time.time()
    silences = json.load(open(raw_transcript, encoding="utf-8")).get("audio_silences")
    room = A.room_noise_db(os.path.abspath(video), silences)
    r = A.polish(wav, wav, room_db=room)
    print("== Polishing the sound (%.0fs)" % (time.time() - t))
    nr = r["noise_reduction_db"]
    note = ("noise reduced by %.0f dB (room at %.0f dB, would have been %.0f dB after the lift)"
            % (nr, r["room_noise_db"], r["noise_after_lift_db"]) if nr else
            "no noise reduction needed (room at %s dB)" % r["room_noise_db"])
    summary = ("%.1f -> %.1f LUFS, peak %.1f dBTP; %s; timing %s"
               % (r["before_lufs"], r["after_lufs"], r["after_true_peak"], note,
                  "exact" if abs(r["offset_samples"]) < 240 else
                  "OFF BY %d SAMPLES" % r["offset_samples"]))
    print(summary + "\n")
    if abs(r["offset_samples"]) >= 240:
        raise SystemExit("The polished sound is out of step with the picture. Re-run with "
                         "--no-audio-polish and report this.")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="The raw video.")
    ap.add_argument("--transcript", required=True, help="Transcript of the raw video (Step 1).")
    ap.add_argument("--speech-cuts", help="The restarts, fumbles and tangents to remove, decided "
                    "by reading the transcript (rough-cut Step 5). Omit if there are none.")
    ap.add_argument("--out", required=True, help="Output folder for the deliverables.")
    ap.add_argument("--scratch", required=True, help="Working folder, outside the user's folders.")
    ap.add_argument("--name", help="Base name for the outputs; defaults to the video's name.")
    ap.add_argument("--no-audio-polish", action="store_true",
                    help="Leave the sound exactly as recorded. Only when the user asks.")
    ap.add_argument("--slides", action="store_true",
                    help="Slides will be added: also render the trimmed copy the cutout needs, "
                         "and leave the zooms until after the slides.")
    args = ap.parse_args()

    name = args.name or Path(args.video).stem
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.scratch, exist_ok=True)
    t0 = time.time()

    sil = os.path.join(args.scratch, "cutlist_silence.json")
    _run("Silence cuts", [_script("rough-cut", "plan_cuts.py"), args.transcript, sil])

    speech = args.speech_cuts
    if not speech:
        speech = os.path.join(args.scratch, "cutlist_speech.json")
        with open(speech, "w", encoding="utf-8") as f:
            json.dump({"cuts": []}, f)

    cut_args = [_script("rough-cut", "render_cuts.py"), args.video, args.transcript, args.out,
                "--cutlist", sil, "--cutlist", speech, "--basename", name]
    if not args.slides:
        cut_args.append("--no-trimmed-video")
    _run("The cut", cut_args)

    xml = os.path.join(args.out, name + ".xml")
    wav = os.path.join(args.out, name + "_audio.wav")
    cut_t = os.path.join(args.scratch, "%s_cut_transcript.json" % name)
    _run("Transcribing the cut", [_script("rough-cut", "transcribe.py"), wav, cut_t,
                                  "--model", "small"], show=False)

    verify = _run("Checking the cut", [_script("rough-cut", "verify_output.py"), wav,
                                       "--original", args.video, "--xml", xml,
                                       "--check-transcript", cut_t], ok_codes=(0, 1))

    polished = None
    if not args.no_audio_polish and verify.returncode == 0:
        polished = _polish(args.video, args.transcript, wav)

    cands = os.path.join(args.scratch, "zoom_candidates.json")
    if not args.slides:
        _run("Zoom candidates", [_script("slow-zoom", "plan_zooms.py"), xml, cut_t, cands])

    print("=" * 60)
    print("Prepared in %.0fs." % (time.time() - t0))
    print("  timeline:          %s" % xml)
    print("  cut transcript:    %s   (for the zooms and the captions)" % cut_t)
    if polished:
        print("  sound:             %s" % polished)
    if verify.returncode != 0:
        print("\nTHE CUT FAILED ITS CHECK -- see 'Checking the cut' above. Fix it (usually a "
              "speech cut that left a gap) and run prepare again before anything else.")
        return 1
    if "REVIEW:" in (verify.stdout or ""):
        print("\nNext: judge the repeated phrases listed under 'Checking the cut'. A missed "
              "restart goes into the speech cutlist and prepare runs again; deliberate "
              "repetition stays.")
    if args.slides:
        print("\nNext: slide-cutout, then the slow-zoom skill, then finish.py.")
    else:
        print("\nNext: choose zooms from the candidates above into zooms.json, then:")
        print("  python finish.py %s --transcript %s --zooms <scratch>/zooms.json --hook \"...\""
              % (xml, cut_t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
