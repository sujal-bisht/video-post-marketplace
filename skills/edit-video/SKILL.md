---
name: edit-video
description: >
  The front door for editing talking-head video: raw footage in, a finished,
  captioned, post-ready video out, plus an editable Premiere/Resolve timeline.
  By default it cuts the dead air and fumbled takes, cleans up the sound and
  sets it to platform loudness, adds slow zooms and background music, puts on
  captions in the
  user's own brand font and colour, and on vertical video adds
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

## How to talk to the user -- read this first

The user sees only questions and results. Everything else stays out of the chat.

1. **No intro.** No "how this works", no list of what the edit will do, no
   "I've had a look at your video". The first thing the user sees is the first
   question.
2. **Questions are pop-ups, never typed out.** Use the host's question tool --
   in Claude Code `AskUserQuestion`; in the Claude app its own pop-up question
   tool. Several questions go in ONE pop-up. Only if no such tool exists, ask
   in one short line per question.
3. **Short and plain.** A question is a few words ("Brand font?"), each option
   one to four words. No explanations of why you ask, no examples in the
   question, no jargon (LUFS, XML, template).
4. **Ask only what only the user can answer:** brand font, brand colour,
   caption style, the hook line and its style. Nothing else -- never the platform, never
   "anything to turn off?", never "do you want slides?". The defaults below
   are decided; they are not questions.
5. **Problems are a one-line heads-up, then carry on.** A font that cannot be
   fetched, a font missing letters: say it in a line and offer the fix in a
   pop-up. If nothing is wrong, say nothing.
6. **Do not narrate the work.** Which takes you cut, which mood you judged,
   that a transcript is running -- none of it goes in the chat while you ask.
   The handover at the end says what happened, briefly.
7. **Only this plugin's own memory.** The brand comes from `brand.py show` and
   nowhere else: not from memories of other conversations, not from other
   tools' files (a BUSINESS-BRAIN.md, a brand guide elsewhere). Do not mention
   what you know from outside; it does not exist for this edit.

Why all questions come first: the edit takes minutes and people walk away
while it runs. A question halfway through sits unanswered and the work stops.
Why every choice is shown as a picture of their own video: nobody can choose a
caption style from its name, and those pictures are the first results they get
-- seconds in, before the wait -- which is what keeps them with the tool.

## What runs by default -- never asked about

| Step | Default |
|---|---|
| Rough cut: dead air, fumbles, repeated takes | **on** |
| Sound clean-up and platform loudness | **on** |
| Slow zooms | **on** |
| Captions in the brand | **on** |
| Background music | **on**, vertical and horizontal |
| Hook line | **on** for vertical video; horizontal only if the user asks |
| Slides and cutout | **off** -- only when the user's own message asks for it AND gives the slides |

A default is switched off only when the user says so unprompted ("no music",
"don't cut it"): `prepare.py --no-audio-polish`, `finish.py --no-music`,
`--no-captions`, no `--zooms`, no `--hook`. If they ask for slides without
giving the images, ask for the images -- never invent slides.

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
to, and do not report it: only an install that fails gets a one-line heads-up.

## Step 1: ask, before any work

Silently, before the first question:

- Start the transcript **in the background** (the hook is written from it, and
  the rough cut reuses it -- never transcribe the raw video twice):

  ```bash
  python ../rough-cut/scripts/transcribe.py <video> <scratch>/<name>_transcript.json --model small
  ```

- Check the saved brand: `python scripts/brand.py show`.
- Music library, first run on a machine or when new tracks appear:
  `python scripts/music_library.py unlabelled`, and label each from its title
  and numbers with `music_library.py label <file> <mood>`. A mood the user set
  (`music_library.py set`) is never overruled.

### 1a. Pop-up one: the brand

**Brand saved** -- one question:

| Header | Question | Options |
|---|---|---|
| Brand | Same brand as last time? | "Rethink Sans, #E90D41" (the saved one) / "Change it" |

**Nothing saved, or "Change it"** -- two questions in one pop-up:

| Header | Question | Options |
|---|---|---|
| Font | Brand font? | three common Google Fonts (e.g. Montserrat, Poppins, Inter); they type their own under Other |
| Colour | Brand colour? | three clean colours with hex (e.g. "Red #E11D48", "Blue #1D4ED8", "Black #111111"); they type their own hex under Other |

If they describe a colour instead of a hex code, pick a clean hex for it.
Save:

```bash
python scripts/brand.py set --font "Rethink Sans" --colour "#E90D41"
```

`set` refuses rather than guesses (exit code 2). Then a one-line heads-up and a
pop-up, nothing more:

- **Paid font, files not given** (Helvetica Neue, Proxima Nova, Gotham...):
  *"Proxima Nova isn't free to download."* Pop-up: "Upload the font files" /
  "Use Montserrat instead" -- the closest free alternative, by name. Never
  switch fonts without this.
- **Font missing letters** (a demo copy that cannot draw the umlauts):
  *"This copy of the font can't draw some letters."* Pop-up: "Upload the full
  font" / "Use <alternative>".

A Google Font is fetched automatically: nothing to say.

### 1b. The picture, then pop-up two: caption style and hook

Render the four caption styles on **their own video, in their brand**:

```bash
python scripts/preview_templates.py <their video> --language de
```

Seconds; writes a still (all four, numbered) and a short moving clip to
`~/.video-post/previews/`. `--language de` for German footage.

**Show both in the chat, before the pop-up.** Send the still AND the moving
clip into this conversation with the session's file tool -- in the Claude app
`SendUserFile` with `display: "render"`, both files in one call. Nothing opens
in the computer's own viewer: the user stays in the session, and the previews
appear where the questions are. Never point them at a folder or a file path.

Only if this session has no way to show a file (a plain terminal), rerun the
script with `--open`, which opens them in the computer's viewer instead, and
say so in one line.

The pictures must be on screen before the pop-up appears.

Then ONE pop-up with both questions (vertical video; horizontal has no hook):

| Header | Question | Options |
|---|---|---|
| Captions | Which caption style? | Spotlight / Badge / Impact / Emphasis -- descriptions: "one big word", "one word on a chip", "1-3 words, caps", "phrase, key word in colour". The saved style first, marked "(last time)" |
| Hook | Which opening line? | the three hooks from 1c: label = its first few words, description = the full line. Their own line goes under Other |

If the transcript is not done when the caption picture is ready, ask the
caption style alone and the hook in a second pop-up as soon as it is.

```bash
python scripts/brand.py template badge
```

Whatever hook they choose or type goes in exactly as written.

### 1c. Silently, while they answer: hooks, mood, speech cuts

Read the transcript once, end to end, and in that one reading:

- **Write three hook options** (vertical video) by
  `references/hook-standards.md`: Layer 1 first (for them, what is in it for
  them, worth watching to the end), then Layer 2 from
  `references/hook-library.md`, then re-check Layer 1. Three different
  families. **In the language of the video.**
- **Judge the video's mood** for the music -- `calm`, `warm`, `cinematic`,
  `upbeat`, `moody`. The feel of what is said, not the topic.
- **Decide the speech cuts** by `rough-cut` Step 5's rules (check
  `suspect_words` first, keep deliberate repetition) and write
  `<scratch>/cutlist_speech.json`.

None of this is reported while asking.

### 1d. Pop-up three: the hook style -- shown, then go

As soon as the hook line is chosen, render it on their video in both styles,
side by side and numbered, and pick the music:

```bash
python scripts/preview_hook.py <their video> --hook "<the hook they chose>" \
    --transcript <scratch>/<name>_transcript.json
python scripts/music_library.py pick --mood <the mood> --duration <rough length in seconds>
```

Show the picture in the chat (`SendUserFile`, `display: "render"`; `--open`
only without a file tool), then ONE pop-up:

| Header | Question | Options |
|---|---|---|
| Hook look | Which hook style? | "Brand box" -- brand colour behind the words / "Brand text" -- brand colour words on white or black. The saved style first, marked "(last time)" |

Save it, then one line and go: *"Editing now, with 'City Sunshine' under
it."*

```bash
python scripts/brand.py hook-style box      # or: text
```

The picture is also the first result: they go into the wait having already
seen their video opening, in their brand, looking finished. Horizontal video
has no hook: no picture, no pop-up, just the music line.

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
the cut, verifies it (dead air, timeline sync, leftover restarts), polishes the
sound, and prints the zoom candidates with what is being said in each.

**The sound polish decides for itself** (`lib/audio_polish.py`): rumble below
80 Hz removed; noise reduced only when the room noise -- measured in the raw
recording's own pauses -- would be audible once the level is lifted, and then
only moderately, because an over-cleaned voice sounds robotic; gentle
compression; -14 LUFS, the level Instagram, TikTok and YouTube play at. It
prints what it did. Report that line in the handover, in plain words: "sound
cleaned up and set to platform loudness" or "background hiss reduced". **It does not re-encode
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
    --zooms <scratch>/zooms.json --hook "<the hook they chose>" --music-mood <the mood>
```

To use the exact track you named in the preview, pass `--music <its path>`
instead of `--music-mood`; picking by mood twice can choose differently,
because it avoids the last few tracks used.

**The music** is levelled and ducked from the transcript: about -37 LUFS
while someone speaks, rising to about -32 LUFS in the opening, real pauses and
the ending, with a soft fade in and out -- about 23 dB under the voice, with a dip
in the band where speech is understood, so it is felt rather than heard.
Looped with a crossfade when the video is longer than the track. It is in the final, and on its own two audio tracks in
the timeline (`<name>_media/<name>_music.wav`).

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

Short, in this shape -- nothing else:

> **Done.** Post this one: `<name>_final.mp4`
> For Premiere or Resolve: `<name>.xml` -- keep the whole folder together.
> 1:12 of pauses and retakes cut, 4 zooms, music: *City Sunshine*, sound cleaned up.

Add a line only for something the user must know: a font that was swapped, a
hook that overlaps the face, a check that failed. Keep the folder rule in:
editors find media by searching the folder they import from, so a file that
wanders off shows as Media Offline.

What the folder holds, if they ask:

| File | What it is |
|---|---|
| `<name>_final.mp4` | **The one to post.** Cut, zoomed, captioned, with music. No editing software needed. |
| `<name>.xml` | The editable timeline: every cut and zoom as its own clip; captions, hook and music on their own tracks |
| `<name>_original.<ext>`, `<name>_audio.wav`, `<name>_media/` | What the timeline plays: the original footage (linked, not copied, on the same drive), the cut sound, the caption, hook and music layers. Not for posting |

The final is rendered at 1080 on the short side: Instagram, TikTok, LinkedIn
and Shorts cap short-form at 1080x1920 and re-compress anything bigger. Only if
the user needs a full-size file (a 4K YouTube upload), rerun finish with
`--full-resolution`.

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
10. **Two checks that the sound polish broke.** The noise reducer delays the
    sound by ~25 ms; it is measured and taken out, to the sample. And the
    timeline sync check compared loudness levels, which compression reshapes
    on purpose: it could no longer tell aligned from 0.1 s off. It now compares
    when the sound rises and falls, and catches even a 2-frame (33 ms) shift.
11. **The camera clips imported as Media Offline in Resolve.** Captions, hook
    and sound came in; the picture did not. DJI (and most real cameras) stamp
    the time of day into the file as its start timecode -- 19:51:40 -- and the
    timeline counts from frame 0, so Resolve looked for frame 0 in a file that
    starts at 19:51:40. The original is now copied into the folder without its
    timecode (the data copied as is: no re-encode, frame-identical, seconds),
    which Resolve reads as starting at 00:00:00:00.
12. **Pauses full of breath and head-turns stayed in.** The silence cut only
    takes what is under -45 dB; a breath or turning to read the script is
    louder than that. Between-words pauses are now measured against the
    recording's own speech level and cut down to 120 ms. Their edges come from
    the sound, not the transcript: Whisper stamped the first word of every
    phrase 0.3-0.45 s early, and cutting to its times left most of each pause.
    On the DJI test: 1.36 s before the first word became 0.06 s; the longest
    pause, 1.38 s, became 0.32 s; every word kept.
13. **The hook looked small.** A fixed size with generous padding covered half
    the frame width. It now grows until the box fills ~88% of the width in up
    to three lines, hugs the words, and drops to two wider lines (shorter)
    before it shrinks, when the space above the head is tight.
14. **The hook box had empty space all round with some fonts.** Text width was
    predicted from the font's own metrics, and for Anton the prediction was
    16% too wide, so the box was built around words that were not there:
    almost a capital letter's height of space each side. Every font is now
    measured once by drawing a test line through libass itself
    (`captions._calibration`): Anton's side padding went from 0.89 to 0.32 of
    a capital, as designed. Captions use the same measurement.
