---
name: edit-video
description: >
  The front door for editing talking-head video: raw footage in, a finished,
  captioned, post-ready video out, plus an editable Premiere/Resolve timeline.
  By default it cuts the dead air and fumbled takes, adds slow zooms, and puts
  on captions in the user's own brand font and colour. It asks for the brand
  and the caption style first, then does everything else without asking. Runs
  locally; footage never leaves the machine. Use for ANY request to edit,
  post-produce, trim, tighten, clean up, caption, or "make ready to post" a
  video of someone talking -- Reels, TikToks, Shorts, course lessons, podcast
  clips. Trigger even without the word edit: "clean up my footage", "get rid of
  the pauses", "make this ready for Instagram", "I filmed a video, sort it out",
  "add captions to my raw video".
---

# Edit Video

Raw footage in. A post-ready video out, and a timeline for anyone who edits.

## The rule that shapes everything here

**Ask the brand questions first. Then ask nothing else.** The user answers
three things at the start -- font, colour, caption style -- and after that the
whole edit runs on its own. Every other judgment call (what to cut, where to
zoom, which word to emphasise) is yours to make. An approval queue in the
middle of an edit is the failure this plugin exists to remove.

## What runs by default

| Step | Default | Skip only when |
|---|---|---|
| Rough cut | **on** | the user says not to cut |
| Slow zoom | **on** | the user says no zooms |
| Captions | **on** | the user says no captions |
| Slides and cutout | **off** | -- run it ONLY when the user asks for it AND hands over the slides |

Slides are off by default because most videos have none. If the user asks for
slides but has not given you the images, ask for them. Never invent slides,
and never run the cutout without real assets.

## Step 0: tools, once per machine

```bash
ffmpeg -version
python -c "import faster_whisper, PIL, fontTools"
```

If Pillow or fontTools is missing, install them yourself -- they are small and
the captions need them to measure and verify fonts:

```bash
python -m pip install pillow fonttools
```

If ffmpeg or faster-whisper is missing, install those too. Do not ask the user
to; do it and tell them what you did.

## Step 1: ask, before any work

### 1a. Brand font and brand colour

First check what is saved:

```bash
python scripts/brand.py show
```

**If a brand is saved**, show it in one line and ask one question: *"Same brand
as last time -- Rethink Sans, #E90D41 -- or something different?"*

**If nothing is saved**, ask for both, and say why in one sentence: *"Your
captions go out in your brand, so I need your brand font and your brand
colour."*

Then save it:

```bash
python scripts/brand.py set --font "Rethink Sans" --colour "#E90D41"
python scripts/brand.py set --font "Acme Sans" --font-file A-Regular.otf --font-file A-ExtraBold.otf --colour "#1D4ED8"
```

`set` refuses rather than guesses. Exit code 2 means the question goes back to
the user. The three cases:

- **A Google Font** (Montserrat, Poppins, Rethink Sans...): fetched and saved
  automatically. Nothing to ask.
- **A licensed font** (Helvetica Neue, Proxima Nova, Gotham...): ask the user to
  drop the font files in -- every weight they have, ExtraBold especially. If
  they don't have the files, propose the closest free alternative **by name**
  and wait for a yes. Never switch fonts without saying so.
- **A font that cannot draw every letter** -- usually a demo or trial copy. It
  installs and looks normal, then prints gaps where letters should be. The
  demo Proxima Nova tested here could not draw Ä Ö Ü ß ü, which would have
  broken every German caption. Tell the user which letters are missing and ask
  for the full version, or a free alternative.

If they don't know their hex code, pick a clean one, say which, and move on.

### 1b. The caption style

Show all four templates on **their own video, in their brand**:

```bash
python scripts/preview_templates.py <their video> --language de
```

This opens a still of all four side by side and a short moving clip, because
the templates differ most in rhythm. It takes seconds -- nothing is transcribed
yet. Use `--language de` for German footage so the sample reads naturally.

Then ask: *"Which of these four do you want?"*

| | Template | What it does |
|---|---|---|
| 1 | Spotlight | one word at a time, large, in the brand colour |
| 2 | Badge | one word at a time on a tight rounded brand-colour chip |
| 3 | Impact | one to three words, uppercase, white |
| 4 | Emphasis | a short phrase, the key word in the brand colour |

```bash
python scripts/brand.py template badge
```

The choice is saved. Next time, confirm it in the same breath as the brand.

**Now stop asking, and start working.**

## Step 2: the edit, in this order

The order matters. Each step writes into the same timeline, and the later ones
read what the earlier ones made.

1. **Rough cut** -- follow the `rough-cut` skill end to end, including its
   verification with `--xml`. It leaves `<name>_trimmed.mp4`, `<name>.xml` and
   `<name>_audio.wav`, plus a transcript of the trimmed video from its verify
   step. Keep that transcript: the zooms and the captions both need it.
2. **Slides and cutout** -- only if asked for, with assets. Follow `slide-cutout`.
3. **Slow zoom** -- follow `slow-zoom`, after the slides so no zoom hides under
   one. It adapts to length on its own: short-form gets shorter, closer moves.
4. **Finish** -- captions and the post-ready video:

```bash
python scripts/finish.py <out>/<name>.xml --video <out>/<name>_trimmed.mp4 \
    --transcript <scratch>/trimmed_transcript.json
```

`finish.py` renders the whole edit in one pass, and checks ffmpeg's own log to
prove the brand font was drawn. If it prints FAIL, do not hand the video over.

## Step 3: hand it over

| File | What it is |
|---|---|
| `<name>_final.mp4` | **The one to post.** Cut, zoomed, captioned. No editing software needed. |
| `<name>.xml` | The editable timeline for Premiere or Resolve: every cut, zoom and slide as its own clip, captions on their own top track |
| `<name>_trimmed.mp4`, `<name>_audio.wav`, `<name>_media/` | What the timeline plays. Not for posting |

Say it in that order, and say: **keep everything in this folder together.**
Editors find media by searching the folder they import from, so a file that
wanders off shows as Media Offline.

The final is rendered at 1080 on the short side. Instagram, TikTok, LinkedIn
and Shorts all cap short-form at 1080x1920 and re-compress anything bigger, so
rendering 4K for them is time thrown away. If the user needs a full-size file
-- a 4K YouTube upload -- rerun finish with `--full-resolution`.

## Failure modes that actually happened while building this

1. **ExtraBold rendered as Bold, and nobody noticed.** A font file answers only
   to the family name written inside it, and by convention only Regular and
   Bold carry the plain name; ExtraBold calls itself "Rethink Sans ExtraBold".
   Asking for "Rethink Sans at 800" returned Bold on every caption, for two
   rounds of feedback. Fixed by requesting each face by its own name -- and
   `finish.py` now reads which file ffmpeg actually drew.
2. **Google Fonts answered yes to fonts it does not have.** Ask for "Helvetica
   Neue" and it serves a generated stand-in labelled with that name. Only files
   from the real library are accepted now.
3. **A zoom that did nothing.** Scaling by an expression of time is read once
   when ffmpeg starts, so the final came out flat at 100% while the timeline
   said 109%. The final is rendered with `perspective` now, and was measured
   against the timeline within half a percentage point.
4. **The caption track inside a clip.** Every clip in the XML nests its own
   video block; inserting after the first closing tag put the caption track
   inside the first clip's file description. The insert now counts nesting.
5. **Samples out of sync by two seconds.** Seeking with `-ss` before the input
   resets timestamps, so captions draw what belongs at 0:00. Never seek that way
   when captions are involved.
6. **Previews that looked worse than the real thing.** Shrinking and
   compressing red text smeared it into a ghost beside the letters. The still
   preview is now drawn straight to PNG.
