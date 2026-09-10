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

**`plan_zooms.py` finds what is eligible** -- long enough to breathe (8-14s),
spaced out (one per ~45s), not under a slide, not in the opening seconds. That
is bookkeeping across a whole video, and code does not get bored doing it.

**You choose from those candidates by reading what she is saying.** Zoom on the
moment that carries weight: the point being made, the payoff, the "here's why
this matters", the reassurance the whole section was building to. Leave the
setup, the throat-clearing and the throwaway lines wide. A push-in is emphasis;
emphasis everywhere is emphasis nowhere.

Most videos want **two or three**. If every candidate looks worth zooming, none
of them are -- pick the strongest and move on.

## The look, and why it is what it is

100% to 108%, linear, then easing back to 100% over the last two seconds.

**Why it comes back out.** In ordinary editing a zoom can simply end on a cut,
because a cut is a visual break that hides the change of framing. The cuts here
are not: `rough-cut` removes silence from one continuous shot, so an edit point
is invisible, and dropping from 108% to 100% across one reads as a glitch. The
one exception is a junction where a lot of material was removed -- that is a
real discontinuity, and framing resets there for free. `apply_zooms.py` spots
those on its own and holds the zoom instead of easing it.

**Why only 8%.** On a 4K sequence a zoom is an upscale of the source. Gentle
reads as intentional; strong reads as a gimmick and starts to soften the
picture. `--peak-pct` goes up to 120 and is capped there.

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

Tunables: `--min-len`, `--max-len`, `--min-gap`, `--skip-head`. Leave them alone
unless the user asks for more or fewer zooms; they encode the pacing.

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
5. **Too many zooms.** Constant motion reads as cheap and gives the viewer
   nothing to notice. The 45-second spacing is a floor, not a target.
6. **Zooming a face that is off-centre.** A centred push drifts her out of
   frame. Look at the first video of a batch and set `--centre-x`.
