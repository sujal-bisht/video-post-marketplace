"""The background-music library: what is in it, what mood each track is, which one fits.

    python music_library.py show                    every track, measured, with its mood
    python music_library.py unlabelled              tracks whose mood is still a guess
    python music_library.py label <file> <mood>     the model's mood for a track
    python music_library.py set <file> <mood>       the USER's mood for a track (never overruled)
    python music_library.py pick --mood calm --duration 95
    python music_library.py folder <path>           add a folder of the user's own tracks
    python music_library.py folder --remove <path>

Moods: calm, warm, cinematic, upbeat, moody.

The starter tracks ship with the plugin (lib/music/). Folders added here are
the user's own and stay on their machine: they are read where they are, never
copied into the plugin.
"""
import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parents[2] / "lib"):
    if (_candidate / "music.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
import music as M  # noqa: E402

CONFIG = os.path.join(M.HOME, "music.json")


def folders():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return [p for p in json.load(f).get("folders", []) if os.path.isdir(p)]
    except Exception:
        return []


def _save_folders(paths):
    os.makedirs(M.HOME, exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump({"folders": paths}, f, indent=2)


def _line(t):
    return ("%-48s %5.0fs  %4.0f bpm  bright %4d Hz  density %.2f  dyn %4.1f dB  -> %-9s (%s)"
            % (t["title"][:48], t["duration"], t["bpm"], t["brightness_hz"], t["density"],
               t["dynamics_db"], t["mood"], t.get("mood_source", "measured guess")))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    sub.add_parser("unlabelled")
    for name in ("label", "set"):
        p = sub.add_parser(name)
        p.add_argument("file")
        p.add_argument("mood", choices=M.MOODS)
    p = sub.add_parser("pick")
    p.add_argument("--mood", required=True, choices=M.MOODS)
    p.add_argument("--duration", type=float, required=True, help="Seconds of finished video.")
    p = sub.add_parser("folder")
    p.add_argument("path")
    p.add_argument("--remove", action="store_true")
    args = ap.parse_args()

    if args.cmd == "folder":
        path = os.path.abspath(args.path)
        current = folders()
        if args.remove:
            current = [p for p in current if os.path.abspath(p) != path]
        elif not os.path.isdir(path):
            raise SystemExit("No such folder: %s" % path)
        elif path not in current:
            current.append(path)
        _save_folders(current)
        print("Music folders: %s" % (", ".join(current) or "none (starter tracks only)"))
        tracks = M.library(current)
        print("%d track(s) in the library." % len(tracks))
        return 0

    tracks = M.library(folders())
    if args.cmd in ("show", "unlabelled"):
        shown = [t for t in tracks if args.cmd == "show" or
                 t.get("mood_source", "measured guess") == "measured guess"]
        if not shown:
            print("Every track has a mood." if tracks else
                  "The library is empty: no starter tracks and no music folder.")
            return 0
        for t in shown:
            print(_line(t))
            print("      %s" % t["path"])
        if args.cmd == "unlabelled":
            print("\nLabel each from its title and these numbers (music_library.py label):")
            for mood in M.MOODS:
                print("  %-9s %s" % (mood, M.MOOD_HELP[mood]))
        return 0

    if args.cmd in ("label", "set"):
        match = [t for t in tracks if t["path"] == os.path.abspath(args.file)
                 or os.path.basename(t["path"]) == os.path.basename(args.file)]
        if not match:
            raise SystemExit("Not in the library: %s" % args.file)
        ok = M.set_mood(match[0]["path"], args.mood, "user" if args.cmd == "set" else "model")
        print(("%s -> %s" % (match[0]["title"], args.mood)) if ok else
              "%s keeps the mood the user gave it (%s)." % (match[0]["title"], match[0]["mood"]))
        return 0

    if args.cmd == "pick":
        t = M.pick(args.mood, args.duration, folders())
        if not t:
            print("NO MUSIC: the library is empty.")
            return 2
        print("Picked: %s  (%s, %.0fs%s)" % (t["title"], t["mood"], t["duration"],
              "" if t["mood"] == args.mood else "; nothing is labelled %s, this is the closest"
              % args.mood))
        print("Path:   %s" % t["path"])
        return 0


if __name__ == "__main__":
    sys.exit(main())
