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
circle 22% of frame width, a 6px ring, bottom-right, and a margin of 2.5%
of frame width — all
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

**Sizes and margins are percentages of frame width, never pixels.** A 48px
margin is 2.5% of a 1920-wide frame but 1.25% of a 3840-wide one, so identical
numbers gave 4K footage a different look from 1080p. The diameter is also
clamped to 8-30% of frame width: past that a corner cutout stops reading as one,
and a run on a real 4K clip came back at 36% of frame width sitting mid-right,
with 390px margins, while the arguments said 22% and bottom-right.

The renderer then **measures the file it just wrote** — the bounding box of
the alpha channel — and refuses to hand over an overlay whose circle is not
the requested size in the requested corner. Parameters describe intent; the
bounding box is what the user actually sees.

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

Options worth knowing: `--diameter-pct` (clamped to 8-30), `--border-px`,
`--position`, `--margin-pct`, `--codec`, and `--handles` (extra seconds of
overlay either side of each range, so the cutout can be extended in the editor
without a re-render). `--margin-px` overrides the margin in absolute pixels;
prefer the percentage, since a pixel margin does not survive a change of
resolution.

`--crop-offset-x` shifts the square crop when the speaker does not sit dead
centre in frame, so the circle does not clip them. Framing is usually consistent
across a shoot, so one value serves a whole batch — check the first video and
reuse it.

### Step 5: Verify — not optional

```bash
python scripts/verify_cutout.py <output_dir>/<name>.xml --resolved <scratch>/resolved.json
```

Checks the things that are invisible in the XML and obvious on import: missing
media, overlapping clips, overlays that do not match their slide, overlays
missing an alpha channel (which would show as a solid rectangle instead of a
circle), and a camera track that ends before the sequence does.

It also checks **lip sync** -- whether V1 actually plays what the rough cut
plays -- by comparing the audio each clip reads against the audio at that moment
in the rendered cut. It finds `<name>_trimmed.mp4` beside the XML on its own;
pass `--rendered-cut` if it lives elsewhere. If it says the check was skipped,
do not treat the timeline as verified: sound drifting from picture is invisible
to every other check here, and it reached a user that way once.

Report its numbers, not your intentions.

### Step 6: Hand it over

Two things land in the output folder:

- `<name>.xml` — the one timeline to import
- `<name>_media/` — the overlays and slide videos it references
- `<name>_trimmed.mp4` and `<name>_audio.wav` — the rough cut's own
  files, still referenced by the timeline's picture and sound

This step **replaces** the rough cut's `<name>.xml` with the merged timeline and
deletes nothing else, so the folder holds exactly one XML. Two files in a folder
invite two imports no matter what the instructions say -- telling the user "only
import this one" did not work in practice, so there is only one to import.

Tell the user: import the `.xml` and leave everything else in the folder where
it is. Editors resolve media by searching the folders they are given rather than
by trusting the absolute paths in the XML, so every referenced file has to sit
in the output folder — that is why slides are re-encoded into `_media`
instead of being referenced where they were exported from, and why the rough
cut's trimmed video and sidecar wav stay put. The overlays are large (roughly a
gigabyte per 2–3 minutes of slide time) and can be deleted once the final render
is done.

**What they see on import:**

```
V3   circular cutout overlays
V2   slides
V1   the rough cut's own clips -- individual and still adjustable
A1/A2  audio from the trimmed cut, one track per channel, linked to V1
```

Full frame wherever no slide covers her; slide plus cutout where one does. Every
piece is an ordinary clip — drag a slide's edge, delete one, swap one out — with
no effects to apply.

## If zooms are also wanted

Run `slow-zoom` **after** this skill, never before. It reads this timeline to
find the camera clips and the spans where a slide covers them, and a zoom placed
under a slide is invisible -- she is a corner circle there.

Effects already on V1 are carried across when this skill merges, so a zoom added
first is not destroyed. It is simply in the wrong place, which its own verifier
catches. Order still matters; it just fails loudly instead of quietly.

## Failure modes worth knowing

1. **A cue that cannot be found silently loses a slide.** Hence the refusal and
   the printed near-misses. Never let a `not_found` through unmentioned.
2. **A repeated phrase is not a timestamp.** "So let's begin" said three times
   identifies nothing. Flagged as ambiguous, not resolved by coin flip.
3. **Media offline even though the paths are right.** An importer looks for
   media by searching the folders it is pointed at; the absolute pathurl in the
   XML is close to decorative. Proven by scripting Resolve with an empty search
   folder: the import was refused outright. Everything the timeline references
   therefore lives in the output folder.
4. **An overlay without alpha is a black box on screen.** Cheap to check, ugly
   to miss, so Step 5 checks it.
5. **The circle clipping the speaker** when she sits off-centre. `--crop-offset-x`
   exists for this; look at the first video of a batch rather than assuming.
6. **Two timelines instead of one.** Forgetting `--rough-cut-xml` causes it, and
   it looks fine until the user opens the project and finds the cut in one
   sequence and the overlays in another.
7. **Audio importing offline while the video links.** Verified in Resolve: if
   picture and sound both point at the *same* file, the importer invents an
   audio-only view of it, and that phantom entry is what shows up offline. The
   audio therefore references the trimmed cut while the video references the
   original footage -- two distinct real files, nothing to guess. Confirmed by
   scripting the import: 0 offline items, one pool entry per file. A file must
   also declare depth and sample rate, not just a channel count.
8. **Silent media claiming audio.** Overlays and slide videos have no audio
   stream; declaring channels on them sends the importer hunting for essence
   that does not exist.
9. **A cutout at 36% of frame width in the middle-right,** while the arguments
   said 22% bottom-right. Two causes, both now designed out: the margin was in
   fixed pixels, so it meant half as much on a 4K frame, and nothing ever looked
   at the rendered file. Geometry is percentage-based, clamped to 8-30%, and
   verified against the alpha bounding box before hand-off. **No parameter is
   proof of anything until the output has been measured.**
10. **Sound and picture on different clocks.** V1 is copied from the rough cut,
   so a rough cut built with in-points meant for a different file hands this
   skill an out-of-sync camera track and everything downstream inherits it. The
   whole structural suite passed while lips ran seconds off the audio. Step 5
   now compares audio content, and a rough cut made before that fix should be
   re-rendered rather than reused.
11. **Accented words splitting into two tokens.** Cue matching tokenised on
   `a-z`, so "glueckliche" became two tokens at the umlaut. Cue and transcript
   were mangled identically, so matches still landed — but token counts
   stopped matching word counts, which quietly distorted the scores and the
   reported positions. Tokenisation is Unicode-aware now, which matters because
   this footage is German and accents are the norm, not an edge case.
