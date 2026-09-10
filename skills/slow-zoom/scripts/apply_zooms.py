"""Write chosen zooms into the timeline that already exists.

There is exactly ONE XML in an output folder, and this edits it in place. It does
not produce a timeline of its own. Two files in a folder means two imports no
matter what the instructions say -- that lesson was learned twice already.

Input is the list of spans chosen from plan_zooms.py's candidates:
{"zooms": [{"start": 34.2, "end": 46.0, "reason": "the payoff line"}]}

Usage:
    python apply_zooms.py <timeline.xml> <zooms.json>
        [--peak-pct 108] [--ramp-out 2.0] [--centre-x 0.0] [--centre-y 0.0]
"""
import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parents[2] / "lib"):
    if (_candidate / "zoom.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
try:
    import zoom
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Cannot find zoom.py -- it should sit beside this script or at "
                     "<plugin root>/lib/zoom.py. Reinstall the video-post plugin. (%s)" % exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline_xml")
    ap.add_argument("zooms_json")
    ap.add_argument("--peak-pct", type=float, default=zoom.DEFAULT_PEAK_PCT,
                    help="How far in to push. The default is deliberately gentle: on a 4K "
                         "sequence a zoom is an upscale of the source, and anything "
                         "stronger starts to read as a gimmick.")
    ap.add_argument("--ramp-out", type=float, default=zoom.DEFAULT_RAMP_OUT_S,
                    help="Seconds spent easing back to 100%% at the end of a zoom. The cuts "
                         "in a rough cut are invisible -- one continuous shot with silence "
                         "removed -- so dropping the framing across one looks like a glitch.")
    ap.add_argument("--centre-x", type=float, default=0.0,
                    help="Shift the zoom centre horizontally when the speaker does not sit "
                         "mid-frame, so pushing in does not drift them out of shot.")
    ap.add_argument("--centre-y", type=float, default=0.0)
    args = ap.parse_args()

    if args.peak_pct > zoom.MAX_PEAK_PCT:
        raise SystemExit("A %.0f%% zoom is past the %.0f%% ceiling. On a 4K sequence this is "
                         "an upscale of the source, and a push that large stops looking like "
                         "emphasis." % (args.peak_pct, zoom.MAX_PEAK_PCT))
    if args.peak_pct < 100.0:
        raise SystemExit("This writes push-ins; a peak below 100%% would push out.")

    tl = zoom.read_timeline(args.timeline_xml)
    with open(args.zooms_json, encoding="utf-8") as f:
        chosen = json.load(f)
    spans = chosen["zooms"] if isinstance(chosen, dict) else chosen
    if not spans:
        raise SystemExit("No zooms listed. Nothing to do.")

    spans = sorted(spans, key=lambda z: float(z["start"]))
    hard = set(tl and zoom.real_discontinuities(tl["v1"]) or [])

    # Refuse a plan that would look wrong, rather than writing it and hoping.
    problems = []
    for a, b in zip(spans, spans[1:]):
        if float(b["start"]) < float(a["end"]):
            problems.append("zooms at %.2fs and %.2fs overlap" % (a["start"], b["start"]))
    for z in spans:
        s, e = float(z["start"]), float(z["end"])
        if e <= s:
            problems.append("zoom at %.2fs has no length" % s)
        if e > tl["duration"] + 0.05:
            problems.append("zoom ends at %.2fs, past the end of the timeline (%.2fs)"
                            % (e, tl["duration"]))
        for c_start, c_end in tl["covered"]:
            if e > c_start and s < c_end:
                problems.append("zoom %.2f-%.2fs sits under a slide, where the camera is a "
                                "corner circle and a zoom is invisible" % (s, e))
    if problems:
        raise SystemExit("Refusing to write these zooms:\n  " + "\n  ".join(problems))

    ramps = []
    for z in spans:
        s, e = float(z["start"]), float(z["end"])
        # A zoom that happens to end on a junction where a lot was cut can stop
        # dead: the picture already jumps there, so the framing reset is free.
        ends_on_jump = any(abs(e - c) < 0.25 for c in hard)
        ramps.append(zoom.Ramp(s, e, args.peak_pct, args.ramp_out, hard_out=ends_on_jump))

    text = open(args.timeline_xml, encoding="utf-8").read()
    new_text, touched = zoom.apply_to_xml(text, ramps, tl["fps"],
                                          centre=(args.centre_x, args.centre_y))
    with open(args.timeline_xml, "w", encoding="utf-8") as f:
        f.write(new_text)

    def fmt(t):
        return "%d:%05.2f" % (int(t // 60), t % 60)

    print("Wrote %d zoom(s) into %s" % (len(ramps), os.path.basename(args.timeline_xml)))
    for z, r in zip(spans, ramps):
        print("  %s -> %s   100%% -> %.0f%%%s%s"
              % (fmt(r.start), fmt(r.end), r.peak,
                 "" if r.ramp_out else "  (holds; ends on a real jump)",
                 "   %s" % z.get("reason", "") if z.get("reason") else ""))
    print("%d camera clip(s) carry a slice of a ramp." % touched)
    print("\nStill one timeline: this edited the existing file rather than adding another.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
