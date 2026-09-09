"""
Measure a built slide timeline before handing it over.

The failures that matter here are all invisible in the XML and obvious the
moment an editor opens it: media that will come up offline, overlays whose
length does not match the slide they belong to, slides that overlap each other,
overlays with no alpha channel (a black square instead of a circle), or a
timeline shorter than the video.

Exit code is 1 if anything would actually go wrong on import, so a broken
timeline is noisy rather than silent.

Usage:
    python verify_cutout.py <timeline.xml> [--resolved resolved_slides.json]
        [--tolerance-frames 1]
"""
import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse


def probe(path, entries):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", entries, "-of", "default=noprint_wrappers=1", path],
        text=True)
    return dict(l.split("=") for l in out.strip().splitlines() if "=" in l)


def path_from_url(pathurl):
    p = unquote(urlparse(pathurl).path)
    # file://localhost/C:/... -> /C:/... on Windows
    if len(p) > 2 and p[0] == "/" and p[2] == ":":
        p = p[1:]
    return p


def fmt(frames, timebase):
    t = frames / timebase
    return "%d:%05.2f" % (int(t // 60), t % 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline_xml")
    ap.add_argument("--resolved", default=None,
                    help="resolved_slides.json, to confirm the timeline matches the plan.")
    ap.add_argument("--tolerance-frames", type=int, default=1,
                    help="Allowed difference when comparing planned times to the timeline; "
                         "one frame is the inherent rounding of a frame-addressed format.")
    args = ap.parse_args()

    root = ET.parse(args.timeline_xml).getroot()
    seq = root.find("sequence")
    timebase = int(seq.findtext("rate/timebase"))
    seq_frames = int(seq.findtext("duration"))
    tracks = seq.findall("./media/video/track")
    audio_tracks = seq.findall("./media/audio/track")

    print("Timeline: %s" % args.timeline_xml)
    print("%d video track(s), %d audio track(s), %s long at %dfps"
          % (len(tracks), len(audio_tracks), fmt(seq_frames, timebase), timebase))

    ok = True

    # Resolve every referenced file once; ids are defined on first use.
    files = {}
    for f in seq.findall(".//file"):
        fid = f.get("id")
        url = f.findtext("pathurl")
        if fid and url:
            files[fid] = path_from_url(url)

    missing = [(fid, p) for fid, p in files.items() if not os.path.isfile(p)]
    if missing:
        ok = False
        print("\nFAIL: %d referenced file(s) do not exist -- these come up offline:" % len(missing))
        for fid, p in missing:
            print("    %s" % p)
    else:
        print("\nAll %d referenced file(s) exist." % len(files))

    def clips_of(track):
        out = []
        for c in track.findall("clipitem"):
            fid = c.find("file").get("id") if c.find("file") is not None else None
            out.append({
                "id": c.get("id"), "name": c.findtext("name"),
                "start": int(c.findtext("start")), "end": int(c.findtext("end")),
                "in": int(c.findtext("in")), "out": int(c.findtext("out")),
                "file": files.get(fid),
            })
        return sorted(out, key=lambda x: x["start"])

    layers = [clips_of(t) for t in tracks]
    names = ["V%d" % (i + 1) for i in range(len(layers))]
    for name, clips in zip(names, layers):
        print("  %s: %d clip(s)" % (name, len(clips)))

    # Overlapping clips on one track cannot happen in an editor and mean the plan
    # produced ranges that collide.
    for name, clips in zip(names, layers):
        overlaps = [(a, b) for a, b in zip(clips, clips[1:]) if a["end"] > b["start"]]
        if overlaps:
            ok = False
            print("\nFAIL: %s has %d overlapping clip pair(s):" % (name, len(overlaps)))
            for a, b in overlaps[:5]:
                print("    %s ends %s but %s starts %s"
                      % (a["name"], fmt(a["end"], timebase), b["name"],
                         fmt(b["start"], timebase)))

    # Slides (V2) and overlays (V3) should line up one-to-one.
    if len(layers) >= 3:
        slides, overlays = layers[1], layers[2]
        if len(slides) != len(overlays):
            ok = False
            print("\nFAIL: %d slide(s) but %d overlay(s) -- they should pair up."
                  % (len(slides), len(overlays)))
        else:
            bad = []
            for s, o in zip(slides, overlays):
                if abs(s["start"] - o["start"]) > args.tolerance_frames or \
                   abs(s["end"] - o["end"]) > args.tolerance_frames:
                    bad.append((s, o))
            if bad:
                # Only a warning: handles deliberately extend an overlay past its
                # slide so the editor can lengthen it without a re-render.
                print("\nNOTE: %d overlay(s) do not exactly span their slide. Expected if "
                      "--handles was used; suspicious otherwise:" % len(bad))
                for s, o in bad[:5]:
                    print("    slide %s-%s vs overlay %s-%s"
                          % (fmt(s["start"], timebase), fmt(s["end"], timebase),
                             fmt(o["start"], timebase), fmt(o["end"], timebase)))
            else:
                print("Slides and overlays line up exactly.")

        # An overlay without alpha renders as an opaque rectangle, not a circle.
        no_alpha = []
        for o in overlays:
            if not o["file"] or not os.path.isfile(o["file"]):
                continue
            info = probe(o["file"], "stream=pix_fmt")
            pix = info.get("pix_fmt", "")
            if "a" not in pix:
                no_alpha.append((o["name"], pix))
        if no_alpha:
            ok = False
            print("\nFAIL: %d overlay(s) carry no alpha channel -- they will show as solid "
                  "rectangles:" % len(no_alpha))
            for n, pix in no_alpha:
                print("    %s (pix_fmt=%s)" % (n, pix))
        elif overlays:
            print("All overlays carry an alpha channel.")

    # The camera on V1 should cover the whole sequence, or the timeline ends early.
    if layers and layers[0]:
        cam_end = max(c["end"] for c in layers[0])
        if cam_end < seq_frames - args.tolerance_frames:
            ok = False
            print("\nFAIL: camera track ends at %s but the sequence runs to %s."
                  % (fmt(cam_end, timebase), fmt(seq_frames, timebase)))

    if args.resolved:
        with open(args.resolved, encoding="utf-8") as f:
            plan = json.load(f)
        planned = plan["slides"]
        slides = layers[1] if len(layers) > 1 else []
        if len(planned) != len(slides):
            ok = False
            print("\nFAIL: plan has %d slide(s), timeline has %d."
                  % (len(planned), len(slides)))
        else:
            drift = []
            for p, s in zip(planned, slides):
                want = int(round(p["start"] * timebase))
                if abs(want - s["start"]) > args.tolerance_frames:
                    drift.append((p, s, want))
            if drift:
                ok = False
                print("\nFAIL: %d slide(s) are not where the plan put them:" % len(drift))
                for p, s, want in drift[:5]:
                    print("    planned %s, timeline %s"
                          % (fmt(want, timebase), fmt(s["start"], timebase)))
            else:
                print("Every slide sits where the plan put it.")

    if ok:
        print("\nPASS: media all present, tracks well-formed, overlays have alpha, "
              "nothing overlaps.")
        return 0
    print("\nThis timeline has problems that will show on import. Fix before handing it over.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
