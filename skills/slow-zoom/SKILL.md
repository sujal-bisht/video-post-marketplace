---
name: slow-zoom
description: >
  Adds slow push-ins to a talking-head timeline -- the gentle zoom that lands on
  the moments that carry weight and leaves the rest wide. Places them
  automatically by reading the transcript; the user is never asked where. Writes
  keyframed scale into the FCP7 XML timeline that already exists, so Premiere
  Pro and DaVinci Resolve both show an ordinary adjustable effect on ordinary
  clips, and nothing is re-rendered. Use whenever the user wants to: add zooms
  or push-ins to talking-head footage, make a static shot feel less flat, add
  movement or "some life" to a locked-off camera, or emphasise key moments in a
  lesson. Trigger even without the word zoom -- e.g. "it feels static", "add
  some movement", "punch in on the important bits", "make it less boring to
  watch".
---

# Slow Zoom

A slow push-in on the lines that matter, and nothing on the lines that do not.

## Run this last

After `rough-cut`, and after `slide-cutout` if slides are being used. Two
reasons, both learned by doing it in the wrong order:

- Zooming the camera track underneath a slide is invisible -- she is a small
  circle in the corner there. The planner skips covered spans, but it can only
  skip them if the slides already exist.
- Everything writes into the same single XML, and this reads that XML to find
  the clips and the slide spans.

Running out of order is caught rather than shipped: `verify_zooms.py` fails on a
zoom that ended up buried under a slide.

## The user is never asked where to zoom

This matters enough to state plainly. Deciding where a push-in belongs is
exactly the mental work that doing it by hand costs. A skill that asks "where
would you like the zooms?" has automated nothing.

So placement is decided here, the same way filler cuts and restarts are decided
in `rough-cut`: arithmetic narrows it down, judgment picks.

**`plan_zooms.py` lays out the cadence** -- a zoom, a quiet stretch, a zoom --
at the lengths and spacing in the standards below, skipping anything under a slide
or in the opening seconds. It prints the coverage that keeping every candidate
would give, and **how many you must keep to hit the target**.

**You choose from those candidates by reading what she is saying.** Zoom on the
moment that carries weight: the point being made, the payoff, the "here's why
this matters", the reassurance the whole section was building to. Leave the
setup, the throat-clearing and the throwaway lines wide. A push-in is emphasis;
emphasis everywhere is emphasis nowhere.

**Keep at least the number the planner asks for.** The candidates are already
spaced to land on the target, so dropping them is what makes a video too sparse
-- on the first real test three of four were kept and coverage fell to 20%. Drop
one only when it genuinely carries nothing, and when you do, the verifier will
tell you if the pacing fell out of range.

## The standards

These are the numbers, and they are not suggestions -- the first release got the
smoothness perfect and the pacing wrong, which is just as visible.

| | value | why |
|---|---|---|
| Strength | **110%**, every time | One standard. Zooms that differ slightly read as inconsistency, not variety. |
| Length | **12-16s** | Long enough to feel like a drift rather than a move. |
| Quiet between | **14s** minimum | Below this the frame never settles. |
| Coverage | **~50% of eligible time** | Half moving, half still. |
| First zoom | **by 0:20** | A video that opens static reads as static. |

**Coverage is the number that matters; length and spacing are only the knobs
that produce it, and they have to agree.** Half the runtime moving means the
zooms and the still stretches are about equal, so a 14s gap needs a 14s zoom.
Shorter zooms at the same spacing land near 40%, which is what the first attempt
did: 10-14s zooms 45s apart gave three isolated events in three minutes, 20% of
the runtime moving. It did not read as a style.

Coverage is measured against **eligible** time, not total runtime -- stretches
where a slide covers the camera cannot move, whatever the setting says.

`verify_zooms.py` fails a video outside 38-62%, so a timeline that drifted too
sparse or too busy is caught here rather than by the person watching it.

## The shape of one zoom

100% to 110%, linear, then easing back to 100% over the last two seconds.

**Why it comes back out.** In ordinary editing a zoom can simply end on a cut,
because a cut is a visual break that hides the change of framing. The cuts here
are not: `rough-cut` removes silence from one continuous shot, so an edit point
is invisible, and dropping from 110% to 100% across one reads as a glitch. The
one exception is a junction where a lot of material was removed -- that is a
real discontinuity, and framing resets there for free. `apply_zooms.py` spots
those on its own and holds the zoom instead of easing it.

**Why 110% and always the same.** On a 4K sequence a zoom is an upscale of the
source, so there is a real cost to going further; `--peak-pct` is capped at 120.
Varying the strength between zooms was considered and rejected -- small
differences read as inconsistency rather than as variety.

**Why centred, and when not.** `--centre-x` shifts the zoom centre when the
speaker does not sit mid-frame, so pushing in does not drift her out of shot.
Check the first video of a batch and reuse the value, exactly like
`slide-cutout`'s `--crop-offset-x`.

## Workflow

### Step 1: Find the candidates

```bash
python scripts/plan_zooms.py <output_dir>/<name>.xml <scratch>/trimmed_transcript.json \
    <scratch>/candidates.json
```

The transcript must be of the **trimmed** video, so its times match the
timeline. `rough-cut` already made one while verifying its output -- reuse it
rather than transcribing again.

Tunables: `--min-len`, `--max-len`, `--min-quiet`, `--coverage`, `--first-by`,
`--skip-head`. Leave them alone unless the user asks for a different feel; they
encode the standards above. If asked for "more movement", raise `--coverage`
rather than hand-editing the others -- it is the number the rest follow from.

### Step 2: Choose, and write the choice down

Read the text printed with each candidate and keep the ones that carry weight:

```json
{"zooms": [
  {"start": 34.2, "end": 46.0, "reason": "the payoff -- it is not that you are doing something wrong"}
]}
```

The `reason` is for the human reading your report, not for the script. Write one
anyway; if you cannot say why a moment deserves emphasis, it probably does not.

### Step 3: Apply

```bash
python scripts/apply_zooms.py <output_dir>/<name>.xml <scratch>/zooms.json
```

This **edits the existing timeline in place**. It does not write a timeline of
its own -- one XML per output folder, always. Re-running replaces the previous
zooms rather than stacking on them, so it is safe to adjust and apply again.

It refuses a plan that would look wrong: zooms that overlap, zooms past the end
of the timeline, zooms sitting under a slide.

### Step 4: Verify -- not optional

```bash
python scripts/verify_zooms.py <output_dir>/<name>.xml
```

Reconstructs the curve from the finished file and checks the things that are
invisible in an editor until playback: a clip inside a zoom carrying no
keyframes (it plays at 100% and pops), slices that disagree where they meet,
keyframe times written against the wrong clock, a zoom that never returns to
100%, a zoom buried under a slide.

It also checks **pacing**: how much of the eligible runtime moves, and when the
first zoom arrives. A timeline outside 38-62% coverage fails here. That check
exists because the first release passed every other test while being visibly too
sparse to watch.

Report its numbers.

## How this is built, and the rules that came out of testing

**An adjustment layer cannot be shipped.** It is the obvious tool -- one clip
above everything, one ramp, no seams -- and it does not survive the file format.
Resolve was asked to build one and export it: the adjustment clip came back as a
`Solid Color` generator, and re-importing that file put an opaque colour clip on
V1, covering the video. An adjustment layer exists inside a project, never in an
interchange file. Do not try again without re-running that experiment.

**Each clip carries its own slice of one ramp.** The ramp is a straight line in
timeline time; every clip reads its own start and end values off that same line,
so the slices meet exactly by arithmetic. There is nothing to align by hand.

**Keyframe times are absolute sequence frames.** Not frames from the start of
the clip that carries them. Written clip-relative, Resolve imports the file
without complaint and renders a lurch to 106% followed by a near-flat 109%
instead of a smooth climb. Written absolute, a render measured 100.5 / 101.5 /
103.5 / 105.0 / 106.5% against a requested 100.4 / 101.7 / 103.3 / 105.0 /
106.2%. This is the single easiest thing to get wrong here.

**Zoom filters are recognised by shape, not by a marker.** A comment marker was
tried first; `slide-cutout` copies V1 through an XML serialiser when it merges,
serialisers drop comments, and a second run then stacked a second ramp on the
first -- 26 keyframes where there should have been 13. Stripping now looks for a
Basic Motion filter with a keyframed scale, which is what a zoom *is*.

**Costs nothing to render.** Unlike slides and cutouts, a zoom is only numbers
in the XML. No encode, no extra media, no minutes added to the run.

## Failure modes worth knowing

1. **A clip inside a zoom with no keyframes.** It plays at 100% while its
   neighbours are at 107%: a one-clip pop. Step 4 fails on it.
2. **Keyframes on the wrong clock.** Imports cleanly, renders wrong. Measured
   out of Resolve, not theorised.
3. **Applying zooms twice.** Used to double every ramp. Now idempotent --
   verified byte-identical on a second run.
4. **A zoom under a slide.** Invisible work. Run this skill after
   `slide-cutout`; Step 4 fails if one ends up buried.
5. **Wrong density, in either direction.** Too sparse reads as three accidents
   rather than a style; too busy gives the viewer nothing to notice. The first
   release shipped at 20% coverage with 47-second gaps and passed every check,
   because smoothness was measured and density was not. Both are measured now.
6. **Zooming a face that is off-centre.** A centred push drifts her out of
   frame. Look at the first video of a batch and set `--centre-x`.
