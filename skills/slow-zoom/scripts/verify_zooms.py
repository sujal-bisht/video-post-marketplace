"""Read the finished timeline back and check the zooms are actually smooth.

Reconstructing the curve from the file is the only honest check. The arithmetic
that wrote it will always agree with itself; the file is what the editor opens.

What goes wrong, and is invisible without this:
  - A clip inside a zoom with no keyframes plays at 100% while its neighbours
    are at 107%. One clip, one frame boundary, a visible pop.
  - Slices that disagree at a seam, so the ramp steps instead of climbing.
  - Keyframe times written relative to the clip instead of the sequence. That
    imports without complaint and renders a lurch followed by a plateau --
    measured out of Resolve, not theorised.

Usage:
    python verify_zooms.py <timeline.xml> [--peak-pct 108] [--tolerance 0.15]
"""
import argparse
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


def fmt(t):
    return "%d:%05.2f" % (int(t // 60), t % 60)


def zoom_spans(per_clip, fps):
    """Group clips carrying keyframes into the gestures they belong to."""
    spans, current = [], None
    for c in per_clip:
        if c["keys"]:
            if current and abs(c["start"] - current["end"]) < 1.5 / fps:
                current["end"] = c["end"]
                current["clips"].append(c)
            else:
                if current:
                    spans.append(current)
                current = {"start": c["start"], "end": c["end"], "clips": [c]}
        elif current:
            spans.append(current)
            current = None
    if current:
        spans.append(current)
    return spans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline_xml")
    ap.add_argument("--peak-pct", type=float, default=zoom.DEFAULT_PEAK_PCT)
    ap.add_argument("--tolerance", type=float, default=0.15,
                    help="Allowed disagreement, in percentage points, where two clips meet.")
    ap.add_argument("--coverage", type=float, default=0.5,
                    help="Fraction of the runtime that should be moving.")
    ap.add_argument("--coverage-slack", type=float, default=0.12,
                    help="How far from the target is still acceptable.")
    ap.add_argument("--first-by", type=float, default=20.0,
                    help="The first zoom should have started by here.")
    args = ap.parse_args()

    points, fps, per_clip = zoom.read_ramp_from_xml(args.timeline_xml)
    if not points:
        print("No zooms in this timeline.")
        return 0
    covered = zoom.read_timeline(args.timeline_xml)["covered"]

    ok = True
    gestures = []
    timeline = zoom.read_timeline(args.timeline_xml)
    spans = zoom_spans(per_clip, fps)
    print("%d zoom(s) across %d camera clip(s) at %.3f fps:"
          % (len(spans), sum(1 for c in per_clip if c["keys"]), fps))

    for sp in spans:
        keys = sorted(k for c in sp["clips"] for k in c["keys"])
        peak = max(v for _, v in keys)
        # Report the gesture, not the clips carrying it. A clip that merely
        # overlaps the start of a zoom stretches the clip range far past where
        # any movement happens, and a report with misleading numbers in it is
        # how several of this plugin's bugs survived as long as they did.
        moving = [i for i, (_, v) in enumerate(keys) if v > 100.01]
        if moving:
            lo = keys[max(0, moving[0] - 1)][0]
            hi = keys[min(len(keys) - 1, moving[-1] + 1)][0]
        else:
            lo, hi = sp["start"], sp["end"]
        gestures.append((lo, hi))
        print("  %s -> %s   peak %.1f%%   %d clip(s), %d keyframe(s)"
              % (fmt(lo), fmt(hi), peak, len(sp["clips"]), len(keys)))

        # 1. no holes: every clip in the span must carry the ramp
        holes = [c for c in sp["clips"] if not c["keys"]]
        if holes:
            ok = False
            print("    FAIL: %d clip(s) inside this zoom carry no keyframes -- they will "
                  "snap back to 100%% and pop." % len(holes))

        # 2. the slices have to agree where they meet
        for a, b in zip(sp["clips"], sp["clips"][1:]):
            if not (a["keys"] and b["keys"]):
                continue
            left, right = a["keys"][-1][1], b["keys"][0][1]
            if abs(left - right) > args.tolerance:
                ok = False
                print("    FAIL: seam at %s steps from %.2f%% to %.2f%%"
                      % (fmt(b["start"]), left, right))

        # 3. keyframe times must be absolute sequence frames, so they land inside
        #    the clip that carries them
        for c in sp["clips"]:
            for t, _ in c["keys"]:
                if not (c["start"] - 1.0 / fps <= t <= c["end"] + 1.0 / fps):
                    ok = False
                    print("    FAIL: a keyframe at %s sits outside its own clip (%s -> %s) "
                          "-- times were written clip-relative instead of absolute."
                          % (fmt(t), fmt(c["start"]), fmt(c["end"])))
                    break

        # 4. the curve has to start and finish at 100%, or framing is left behind
        first, last = keys[0][1], keys[-1][1]
        if abs(first - 100.0) > args.tolerance:
            ok = False
            print("    FAIL: starts at %.2f%% instead of 100%% -- the push-in begins "
                  "mid-move." % first)
        if abs(last - 100.0) > args.tolerance:
            print("    note: ends at %.2f%% rather than easing back to 100%%. Correct only "
                  "if this lands on a junction where a lot was cut." % last)

        # 5. and stay within the agreed look
        if peak > args.peak_pct + 0.5:
            ok = False
            print("    FAIL: peaks at %.1f%%, past the %.1f%% this was meant to be."
                  % (peak, args.peak_pct))

        # 6. a zoom hidden under a slide is work nobody will ever see. This
        #    happens when the zooms were added before the slides; the planner
        #    excludes covered spans, so running this skill last avoids it.
        hidden = sum(max(0.0, min(hi, c_end) - max(lo, c_start))
                     for c_start, c_end in covered)
        gesture = max(hi - lo, 1e-6)
        if hidden >= gesture - 0.05:
            ok = False
            print("    FAIL: this zoom is completely underneath a slide, where the camera "
                  "is a corner circle. Run slow-zoom AFTER slide-cutout.")
        elif hidden > 0.5:
            print("    note: %.1fs of this zoom sits under a slide and will not be seen."
                  % hidden)

    # 7. zooms must not overlap each other
    for a, b in zip(spans, spans[1:]):
        if b["start"] < a["end"] - 1.0 / fps:
            ok = False
            print("  FAIL: zooms at %s and %s overlap." % (fmt(a["start"]), fmt(b["start"])))

    # 8. pacing. The first release was structurally perfect and still wrong to
    #    watch: three zooms in three minutes, 47s apart, 20% of the runtime
    #    moving. Smoothness was measured; density was not, so nobody noticed.
    duration = timeline["duration"] or 1.0
    moving = sum(hi - lo for lo, hi in gestures)
    coverage = moving / duration
    first = min((lo for lo, _ in gestures), default=None)
    print()
    print("Pacing: %d zoom(s), %.0fs of %.0fs moving (%.0f%%, target %.0f%%); first at %s."
          % (len(gestures), moving, duration, 100 * coverage, 100 * args.coverage,
             fmt(first) if first is not None else "n/a"))
    if coverage < args.coverage - args.coverage_slack:
        ok = False
        print("  FAIL: too sparse. The zooms read as isolated events rather than as a "
              "style. Keep more of the planner's candidates.")
    elif coverage > args.coverage + args.coverage_slack:
        ok = False
        print("  FAIL: too busy. Constantly moving gives the viewer nothing to notice.")
    if first is not None and first > args.first_by:
        print("  note: nothing moves until %s. Fine only if a slide covers the opening."
              % fmt(first))

    print()
    if ok:
        print("PASS: every zoom climbs smoothly, the slices meet, the framing returns, "
              "and the pacing is right.")
        return 0
    print("These zooms will not land as intended. Fix before handing the timeline over.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
