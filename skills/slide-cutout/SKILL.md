---
name: slide-cutout
description: >
  Puts presentation slides on screen with the speaker as a circular cutout in
  the corner, the standard teaching-video look. Slide timing comes from spoken
  cue phrases ("slide 2 starts when I say 'now the second piece'") matched
  against a word-level transcript, so nobody has to enter timestamps. Delivers
  an editable FCP7 XML timeline that Premiere Pro and DaVinci Resolve both
  import -- camera, slides and cutout on separate tracks as ordinary clips, not
  a finished render -- so slide timings stay adjustable. Runs locally, footage
  never leaves the machine. Use whenever the user wants to: add slides or a
  PowerPoint/PDF to a talking-head video, show slides with themselves in a
  corner circle or bubble, make picture-in-picture or PIP over slides, do the
  "teaching video" look, or overlay screen graphics on a lesson. Trigger even
  without these words -- e.g. "put my deck on screen while I talk", "shrink me
  into the corner when the slides come up", "add my presentation to these
  lessons".
---

# Slide Cutout

Slides fill the frame, the speaker sits in a circular corner cutout, and the
whole thing arrives as an editable timeline rather than a finished video.

## Run this after `rough-cut`

Cue phrases are matched against the **trimmed** video's transcript, so slides
land relative to the final edit. Anything the speaker stumbled over is already
gone before slides go on top, and the transcript needed here is the one
`rough-cut` already produced when verifying its output — reuse it rather than
transcribing twice.

## What the user provides

**Slide images** — a folder of PNGs in order. In PowerPoint, `Save As → PNG`
exports one correctly-numbered image per slide. A PDF works too if converted
first, but the PNG export avoids the extra step.

**Cue phrases** — spoken, in their own words: *"slide 1 starts when I say 'the
first framework', slide 2 at 'now the second piece', and go back to full frame
at 'so that's the theory'."* Write that into the plan file yourself; they should
never format JSON.

```json
{
  "slides": [
    {"image": "slide-01.png", "cue": "the first framework"},
    {"image": "slide-02.png", "cue": "now the second piece"}
  ],
  "end_cue": "so that is the theory"
}
```

Only a **start** cue per slide is needed — each runs until the next one, and the
last until `end_cue` or the end of the video. Half the writing, half the chances
of a mistake.

## The look, and why it is baked in

Ask for the brand colour before building; the ring uses it. Defaults are a
circle 22% of frame width, a 6px ring, bottom-right, 48px margin — all
adjustable, and all fixed **inside** the overlay file rather than sent through
the XML.

That is deliberate. An interchange XML cannot carry an elliptical mask, and the
scale/position values it *can* carry are the least reliably translated part of
the format — if the editor ignores them, the cutout opens filling the frame and
needs fixing by hand on every video. Baking the geometry means the clip drops in
at 100% scale and simply looks right.

So: **style is decided once, arrangement stays editable.** Changing the circle's
size or corner means re-rendering overlays (a couple of minutes). Changing *when
a slide appears* is just dragging a clip, or editing one cue phrase and
re-running.

## Workflow

### Step 0: Keep working files out of the user's folder

Transcripts, plans and resolved plans go in a scratch directory. The output
folder should hold only the timeline and its media.

### Step 1: Confirm inputs

The trimmed video, the slide folder (list the images found and show them back),
and the brand colour. Ask for the corner and diameter only if they care;
otherwise use the defaults.

### Step 2: Transcribe, or reuse

```bash
python ../rough-cut/scripts/transcribe.py <trimmed.mp4> <scratch>/t.json --model small
```

Reuse `rough-cut`'s output transcript of the same file if it exists.

### Step 3: Resolve the cue phrases

```bash
python scripts/plan_slides.py <scratch>/t.json <scratch>/plan.json \
    <scratch>/resolved.json --slides-dir <slides folder>
```

Matching is fuzzy, because a remembered phrase never matches a transcript
character for character. But it refuses a weak match instead of guessing, and
flags a cue that occurs twice instead of picking one.

**Read its output before continuing.** A cue reported `not_found` means that
slide will not appear, and it prints what the transcript actually says at the
nearest candidates — usually enough to reword the cue. `ambiguous` means the
phrase is spoken more than once and needs to be longer or more distinctive.
Exit code is non-zero if anything had a problem. Fix cues and re-run rather than
building a timeline with slides silently missing.

### Step 4: Build the timeline

```bash
python scripts/build_timeline.py <trimmed.mp4> <scratch>/resolved.json <output_dir> \
    --basename <name> --border-color "#RRGGBB" \
    --rough-cut-xml <output_dir>/<name>.xml
```

**Always pass `--rough-cut-xml`.** It puts the rough cut's own clips on V1, so
the slides and cutouts land in the *same* timeline as the adjustable cut.
Without it V1 is the flattened trimmed video, the user imports two unrelated
timelines, and the cut can no longer be altered -- which is the whole reason the
rough cut hands back clips in the first place.

Renders one overlay per slide range (not one spanning the whole video — outside a
slide range the cutout is not on screen, so rendering it there would be a
gigabyte of transparent pixels nobody sees), encodes each slide as a short video
into the media folder, and writes the timeline.

Options worth knowing: `--diameter-pct`, `--border-px`, `--position`,
`--margin-px`, `--codec`, and `--handles` (extra seconds of overlay either side
of each range, so the cutout can be extended in the editor without a re-render).

`--crop-offset-x` shifts the square crop when the speaker does not sit dead
centre in frame, so the circle does not clip them. Framing is usually consistent
across a shoot, so one value serves a whole batch — check the first video and
reuse it.

### Step 5: Verify — not optional

```bash
python scripts/verify_cutout.py <output_dir>/<name>_slides.xml --resolved <scratch>/resolved.json
```

Checks the things that are invisible in the XML and obvious on import: missing
media, overlapping clips, overlays that do not match their slide, overlays
missing an alpha channel (which would show as a solid rectangle instead of a
circle), and a camera track that ends before the sequence does.

Report its numbers, not your intentions.

### Step 6: Hand it over

Two things land in the output folder:

- `<name>_slides.xml` — the one timeline to import
- `<name>_media/` — the overlays and slide videos it references

The rough cut's own `<name>.xml` is an input to this step, not something the
user imports. Tell them to import the `_slides.xml` alone, or they end up with
two timelines again.

Tell the user: import the `.xml`, and leave the `_media` folder and the camera
file where they are. An interchange XML holds absolute paths, so moving either
one opens the timeline with media offline. The overlays are large (roughly a
gigabyte per 2–3 minutes of slide time) and can be deleted once the final render
is done.

**What they see on import:**

```
V3   circular cutout overlays
V2   slides
V1   the rough cut's own clips -- individual and still adjustable
A1/A2  camera audio, one track per channel, linked to V1
```

Full frame wherever no slide covers her; slide plus cutout where one does. Every
piece is an ordinary clip — drag a slide's edge, delete one, swap one out — with
no effects to apply.

## Failure modes worth knowing

1. **A cue that cannot be found silently loses a slide.** Hence the refusal and
   the printed near-misses. Never let a `not_found` through unmentioned.
2. **A repeated phrase is not a timestamp.** "So let's begin" said three times
   identifies nothing. Flagged as ambiguous, not resolved by coin flip.
3. **Media offline after tidying up.** Absolute paths are why slides are copied
   next to the XML instead of referenced where they sat.
4. **An overlay without alpha is a black box on screen.** Cheap to check, ugly
   to miss, so Step 5 checks it.
5. **The circle clipping the speaker** when she sits off-centre. `--crop-offset-x`
   exists for this; look at the first video of a batch rather than assuming.
6. **Two timelines instead of one.** Forgetting `--rough-cut-xml` causes it, and
   it looks fine until the user opens the project and finds the cut in one
   sequence and the overlays in another.
7. **Audio importing offline.** A file has to declare its audio essence --
   depth and sample rate, not just a channel count -- and a stereo source needs
   one clipitem per channel rather than one claiming two. Both are handled in
   `lib/fcp7xml.py`; never hand-roll XML that skips them.
8. **Silent media claiming audio.** Overlays and slide videos have no audio
   stream; declaring channels on them sends the importer hunting for essence
   that does not exist.
