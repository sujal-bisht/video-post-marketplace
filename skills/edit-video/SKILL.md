---
name: edit-video
description: >
  The front door for editing talking-head video: raw footage in, a finished,
  captioned, post-ready video out, plus an editable Premiere/Resolve timeline.
  By default it cuts the dead air and fumbled takes, adds slow zooms, puts on
  captions in the user's own brand font and colour, and on vertical video adds
  a hook line for the first seconds. It asks every question first -- brand,
  caption style, hook -- showing each choice on the user's own video, then
  does everything else without asking. Runs
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

**Ask every question first. Then ask nothing else.** The user answers at the
start -- font, colour, caption style, and on vertical video the hook -- and
after that the whole edit runs on its own. Every other judgment call (what to
cut, where to zoom, which word to emphasise) is yours to make.

Why all of it up front: the edit takes minutes, and people walk away while it
runs. A question asked halfway through sits unanswered until they come back,
and the work stops with it. An approval queue in the middle of an edit is the
failure this plugin exists to remove.

**And every choice is shown, not described.** The caption styles are a picture
of their own video in their brand; the chosen hook comes back as a picture of
their video opening with it. Those pictures are also the first results they
get -- seconds in, before the long wait -- and that is what keeps them with
the tool while it works.

## What runs by default

| Step | Default | Skip only when |
|---|---|---|
| Rough cut | **on** | the user says not to cut |
| Slow zoom | **on** | the user says no zooms |
| Captions | **on** | the user says no captions |
| Hook (context line) | **on for vertical video** | the user says no hook. Horizontal: off unless the user asks |
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

### 1-start. Transcribe in the background, right away

The hook is written from what the video says, so it needs a transcript -- and
transcribing is the rough cut's own first step anyway. Start it **in the
background** before asking anything, so it runs while the user answers the
brand questions:

```bash
python ../rough-cut/scripts/transcribe.py <video> <scratch>/<name>_transcript.json --model small
```

The rough cut reuses this file; do not transcribe the raw video twice.

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

### 1b. The caption style -- shown, not described

**Nobody can choose a caption style from its name.** "Spotlight or Emphasis?"
means nothing until they see it. So the question always comes with the
picture: all four templates, on **their own video, in their brand**.

```bash
python scripts/preview_templates.py <their video> --language de
```

It takes seconds (nothing is transcribed) and writes two files to
`~/.video-post/previews/`: a still with all four side by side, numbered 1-4,
and a short moving clip, because the templates differ most in rhythm. Use
`--language de` for German footage so the sample reads naturally.

**Put the still in front of the user, in the conversation, before asking.**

- If this session has a tool for showing the user a file (in the Claude
  desktop app, `SendUserFile` with `display: "render"`), send the still with
  it, and attach the moving clip the same way.
- Otherwise the script has already opened both on their screen. Say so in one
  line, and give the two paths so they can open them again.
- Never ask with only the four names, and never put up a multiple-choice
  picker before the picture is there. The picture is the question.

Then ask: *"Which of the four do you want -- 1, 2, 3 or 4?"* The numbers are
printed on the picture.

| | Template | What it does |
|---|---|---|
| 1 | Spotlight | one word at a time, large, in the brand colour |
| 2 | Badge | one word at a time on a tight rounded brand-colour chip |
| 3 | Impact | one to three words, uppercase, white |
| 4 | Emphasis | a short phrase, the key word in the brand colour |

```bash
python scripts/brand.py template badge
```

The choice is saved. Next time, show the picture again anyway and ask "same
style -- Badge -- or a different one?" in the same message as the brand
check. It costs seconds, and it is the first thing they get to look at.

### 1c. The hook -- vertical video only

Skip this for horizontal video, unless the user asked for a hook there.

Once the background transcript is done, read it end to end and write three
hook options by `references/hook-standards.md`: Layer 1 first (for them, what
is in it for them, worth watching to the end), then Layer 2 from
`references/hook-library.md`, then re-check Layer 1. **In the language of the
video.**

Show the three as plain numbered lines and ask which one -- or their own
words. Ask it in the same message as the caption style when the transcript is
ready by then, so it is one round of questions, not two.

Whatever they choose goes in exactly as they wrote or picked it.

**In the same reading, decide the speech cuts.** You are reading the whole
transcript for the hooks anyway, so this is also when the restarts, fumbles
and repeated takes get picked out, by `rough-cut` Step 5's rules -- check
`suspect_words` first, keep deliberate repetition. Write them to
`<scratch>/cutlist_speech.json` before the user has even answered. One reading
of the transcript, not two.

### 1d. Show them their hook, then go

As soon as the hook is chosen, render it on their video:

```bash
python scripts/preview_hook.py <their video> --hook "<the hook they chose>" \
    --transcript <scratch>/<name>_transcript.json
```

One still, in seconds: the opening of their video with the hook exactly where
the final will put it, and their chosen caption style running underneath, with
their real words. Show it the same way as the caption picture, with one line:
*"This is how your video opens. Starting the edit now -- say stop if you want
anything changed."*

Then start, without waiting for an answer. This is not a fourth question; it
is the first result. The edit runs for minutes with nothing to look at, and
that silence is where people give up on a tool. They should go into the wait
having already seen their video, in their brand, looking finished.

**Now stop asking, and start working.**

## Step 2: the edit -- two commands

The judgments are already made (speech cuts in Step 1c, the rest below); what
is left is machine work, and it runs as two commands, not a step at a time. A
4-minute video once took 28 minutes, and a third of that was not the computer
working but round trips: 68 separate steps where nine would have done.

**1. Prepare** -- the cut, its check, and the zoom candidates:

```bash
python scripts/prepare.py <their video> --transcript <scratch>/<name>_transcript.json \
    --speech-cuts <scratch>/cutlist_speech.json --out <output folder> --scratch <scratch>
```

It plans the silence cuts, builds the timeline and the cut audio, transcribes
the cut, verifies it (dead air, timeline sync, leftover restarts), and prints
the zoom candidates with what is being said in each. **It does not re-encode
the video**: the original is linked into the output folder and both the
timeline and the final read it directly. That one change removed the slowest
step of the whole edit.

Then two judgments, from what it printed:

- **Leftover repeats** under "Checking the cut": a missed restart goes into
  the speech cutlist and prepare runs again; deliberate repetition stays.
- **Zooms**: choose from the candidates by the `slow-zoom` skill's rules --
  keep at least the number it asks for, zoom on the moments that carry weight
  -- and write `<scratch>/zooms.json`.

**2. Finish** -- zooms applied and verified, captions, hook, the final video:

```bash
python scripts/finish.py <output folder>/<name>.xml \
    --transcript <scratch>/<name>_cut_transcript.json \
    --zooms <scratch>/zooms.json --hook "<the hook they chose>"
```

**Slides change the order.** Only when the user asked for slides and gave the
images: run prepare with `--slides` (it then also makes the trimmed copy the
cutout is rendered from, and leaves out the zoom candidates), follow
`slide-cutout`, then `slow-zoom`, then finish without `--zooms`.

`finish.py` renders the whole edit in one pass, and checks ffmpeg's own log to
prove the brand font was drawn. If it prints FAIL, do not hand the video over.

The hook is placed on its own: it finds the speaker's face across the 4.5
seconds it is on screen, allows for any zoom running then, and goes above the
head when there is room, otherwise below the chin -- never over the face,
never over the captions, never under the platform's own buttons. It prints
where it went. If it prints a WARNING that it overlaps the face, say so in the
handover. It stays on for 4.5 seconds with no animation, and the captions keep
running underneath it.

## Step 3: hand it over

| File | What it is |
|---|---|
| `<name>_final.mp4` | **The one to post.** Cut, zoomed, captioned. No editing software needed. |
| `<name>.xml` | The editable timeline for Premiere or Resolve: every cut, zoom and slide as its own clip, captions on their own track, the hook on its own track above that |
| `<name>_original.<ext>`, `<name>_audio.wav`, `<name>_media/` | What the timeline plays: the original footage (linked, not copied, when it is on the same drive -- no extra disk space), the cut sound, the caption and hook layers. Not for posting |

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
7. **28 minutes to cut one video.** On a 6-minute 4K phone clip, the rough
   cut re-encoded the whole video at full resolution before anything else could
   happen -- longer than every other step together. The edit no longer
   re-encodes to cut: the original is linked into the output folder, the
   timeline plays it, and the final reads it directly. Same clip, whole edit:
   about 6 minutes of machine time.
8. **Long videos crashed in three places.** Each worked on short tests and
   broke on a 6-minute video with 300+ cuts: the cut list passed on the
   command line was longer than Windows allows; the zoom written as one nested
   formula was too deep for ffmpeg; the cut written as one formula ran ffmpeg
   out of memory. Graphs now go to ffmpeg as files, each zoom is its own small
   filter, and each kept piece its own trim.
9. **The GPU path was one frame off.** Decoding on the graphics chip is
   measured once per camera and used only when clearly faster and
   frame-identical (`lib/hwdecode.py`). Its scaler re-stamps frames on a
   perfectly regular clock, which phone footage is not, so scaling before the
   cut put the picture a frame early. The cut always runs first, on the
   camera's own timestamps; checked frame by frame against the original.
