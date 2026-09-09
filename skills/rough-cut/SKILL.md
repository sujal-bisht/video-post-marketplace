---
name: rough-cut
description: >
  Produces a tight rough cut of raw talking-head video by removing dead air,
  filler words ("um", "uh", junk sounds), false starts, self-corrections,
  repeated takes, and tangents, while preserving natural style (laughter,
  "you know", "right?"). Editor-agnostic (CapCut, Premiere, DaVinci, or any
  NLE) -- outputs a plain video file plus an optional non-destructive
  companion timeline. Runs entirely locally via Whisper, no cloud APIs,
  footage never leaves the machine. Use whenever the user wants to: trim raw
  footage, remove silence/dead air, cut filler words or "ums and uhs", make a
  rough cut or first-pass edit, batch-process a folder of talking-head videos
  before editing, or turn unedited footage into something ready for an editor.
  Trigger even without "rough cut" -- e.g. "clean up my footage", "get rid of
  the pauses", "I filmed videos and need them trimmed", "cut the dead space
  out of this".
---

# Rough Cut

Turns raw talking-head footage into a tight rough cut: dead air, filler words,
and botched takes removed, delivered as a plain video file that drops into any
editor, plus a companion timeline for Premiere/Resolve users.

## What "tight" means here

The target is **no audible dead air**. When one line ends, the next begins
immediately. The video opens on the first spoken word, not on silence.

This is stricter than it sounds, and it is the single thing most likely to
disappoint the user if you get it wrong. A cut that "removed some silence" is a
failed cut. Read the failure modes section before you start -- it documents real
bugs that shipped a broken edit to a user, and every one of them was invisible
without measuring the output.

## Architecture, and why the work is split this way

Two different kinds of work, deliberately separated by capability:

| Job | Owner | Why |
|---|---|---|
| Finding dead air | `plan_cuts.py` (arithmetic) | Mechanical and high-volume: a 4-minute clip has ~165 silences. Reasoning about them one by one under-counts badly. Code cannot get bored or approximate. |
| Deciding if a line is a botched take | You, reading the transcript | Needs language understanding. No threshold can tell a restart from a rhetorical repeat. |
| Rendering and the companion XML | `render_cuts.py` | Deterministic. |
| Proving it worked | `verify_output.py` | Because intent is not evidence. |

Do not re-implement silence detection in your head or by eyeballing word
timestamps. That path is exactly how this skill failed before.

## One-time setup

Needs `ffmpeg` on PATH plus `faster-whisper`:

```bash
ffmpeg -version
python -c "import faster_whisper"
```

If missing: `pip install faster-whisper`, and install ffmpeg via the system
package manager (`winget install ffmpeg`, `brew install ffmpeg`, etc.).

Optional GPU acceleration (much faster; falls back to CPU automatically if
absent): `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`.

Check this once per environment, not once per run.

## Workflow

### Step 0: Put working files somewhere the user will never see them

Create a scratch directory outside the user's folders and keep every
intermediate there -- transcripts, cutlists, extracted audio snippets:

```bash
python -c "import tempfile; print(tempfile.mkdtemp(prefix='roughcut-'))"
```

The user's output folder must end up containing **only finished deliverables**.
Direct feedback from a real user: stray `transcript.json` and `cutlist_*.json`
files sitting next to the video left them guessing which file they were supposed
to use. Working files are your business, not theirs.

`render_cuts.py` prunes recognised intermediates from the output folder as a
backstop, but do not rely on it -- write them to the scratch directory in the
first place, and delete that directory when the run is done.

### Step 1: Get the input folder

Ask where the raw video files are -- a folder path, or the user can attach a
folder. Never hardcode a path. List the video files found (mp4/mov/mkv/avi) and
show that list back before processing.

### Step 2: Ask how to process

Ask: whole folder as a batch, or a single file? Follow their answer.

### Step 3: Transcribe (also measures real dead air)

```bash
python scripts/transcribe.py <video> <transcript.json> --model small
```

Writes word-level timestamps **and** `audio_silences` -- true dead air measured
from audio levels via ffmpeg `silencedetect`. It prints both the real figure and
the naive word-gap figure so the difference stays visible.

Use `--model medium` if speech is heavily accented or noisy; `base`/`tiny` only
if the user explicitly wants speed over accuracy on a huge batch.

The default `--noise-db -45` is calibrated: at `-40` it shaved the `s` off
"likes" on real footage (sibilants are quiet enough to read as silence), `-30`
eats consonants and breaths outright (one clip measured 64% "silence"), and
`-50` starts missing quiet room tone. Only change it if the footage is unusual,
and say so.

### Step 4: Plan silence cuts (deterministic -- do not hand-roll this)

```bash
python scripts/plan_cuts.py <transcript.json> <cutlist_silence.json>
```

Compresses every measured silence to a 90ms residual (~45ms of guard on each
side of a cut, which keeps quiet consonants intact), trims the lead-in to 20ms
before the first word, and leaves a 120ms tail. Tunables: `--residual-ms`,
`--lead-pad-ms`, `--trail-pad-ms`.

If the user later says the result feels rushed or choppy, raise `--residual-ms`
(150-250 restores a more conversational rhythm). If they say it is still slow,
lower it. This one number is the pacing dial -- reach for it instead of
rewriting logic.

### Step 5: Find botched takes yourself (this is the judgment part)

Read the transcript and write a **second** cutlist of speech to remove:

```json
{"cuts": [
  {"start": 151.46, "end": 152.08, "reason": "restart: 'not just your--' redone as 'not just your ego'"}
]}
```

**Cut:**
- **False starts and self-corrections** -- the speaker begins a line, realises
  it came out wrong, and says it again better. Remove the earlier attempt and
  keep the final clean one. This is the most valuable cut in the whole skill and
  the easiest to miss, so hunt for it actively rather than noticing it in
  passing.
- Explicit correction phrases ("cut that", "scratch that", "let me redo that",
  "start over") plus the flawed run they refer to.
- Repeated takes of the same line -- keep the best/final one.
- Filler words with no content: "um", "uh", and similar formless sounds.
- Rambling tangents that never connect back.

**How to actually find restarts** -- scan for these signatures rather than
waiting for one to be obvious:
0. **Check `suspect_words` first.** transcribe.py flags any single "word"
   lasting over a second. These are not words -- they are material Whisper
   could not align, and they can hide an entire restated sentence. On real
   footage a 4.64s token transcribed as `"one"` actually contained the speaker
   restarting a whole line ("this milestone points to your entire month of
   content at--" then saying it again). **A restart can be completely absent
   from the transcript text.** When a suspect word appears, cut that span out
   of the video and re-transcribe just that span to hear what is really there
   before deciding.
1. **Adjacent near-duplicate phrasing.** Any phrase of ~3+ words that appears
   twice close together is a restart until proven otherwise.
2. **A phrase that ends incomplete, followed by a complete version.** e.g.
   "fill in the--" then "fill it, press play". The abandoned fragment goes.
3. **Grammatically broken run followed by a clean one.** e.g. "this is the
   number matters most" then "this number matters to the business".
4. **A stutter on function words**: "not just your, not just your ego".
5. **Unusually low word confidence** (`prob` under ~0.5) clustered together
   often marks the fumbled attempt; the clean retake scores higher.

**Telling a restart from deliberate rhetoric** -- use whether the repeat
*changes anything*:

- **Changed on the second pass = restart. Cut the first.** The speaker was
  fixing something: "those view means" -> "those views mean"; "fill in the--"
  -> "fill it, press play"; "not just your--" -> "not just your ego"; "points
  to your entire month" -> "points your entire month ... at one business
  number". Completing an abandoned phrase counts as a change.
- **Word-for-word identical repeat = emphasis. Keep both.** "Watching you.
  Watching you." fixes nothing, so it is doing rhetorical work. Same for
  parallel constructions that reuse a phrase deliberately ("you know what
  Tuesday's post is for" / "Tuesday's post is for nothing").

If it is genuinely unclear even after listening, cut the earlier instance: a
redundant line costs little, whereas leaving a botched take in is the exact
failure the user notices. But do not reach for "it's intentional emphasis" just
because the two attempts run together with no pause -- people restart fluidly
all the time, and that reasoning already caused a miss on real footage.

**Never cut:**
- Nervous laughter.
- Personal verbal habits: "you know", "right?", "so yeah", "basically". If the
  filler is a real word or phrase rather than a formless sound, lean toward
  keeping it -- that texture is what makes someone sound like themselves.

Make these calls yourself. Do not present an approval queue; the user
explicitly does not want to review cuts item by item. Surface something only if
a single cut would remove an unusually large stretch of actual speech.

### Step 6: Render

```bash
python scripts/render_cuts.py <video> <transcript.json> <output_dir> \
    --cutlist <cutlist_silence.json> --cutlist <cutlist_speech.json> \
    --basename <name>
```

Cut boundaries are applied exactly as given -- this script adds no padding.
Outputs to `<output_dir>` (default: a `trimmed_output/` folder beside the
input):

- `<name>_trimmed.mp4` -- the merged rough cut. The primary deliverable; works
  in any editor as a single clip.
- `<name>.xml` -- FCP7 XML companion timeline referencing the *original* footage as
  separate back-to-back clips, for non-destructive re-editing in Premiere or
  DaVinci Resolve. It is FCP7 XML (XMEML), not FCPXML: Premiere Pro imports only
  the older format, and Resolve reads it too, so one file serves both editors.
  CapCut cannot import either; skip mentioning it for CapCut-only users. The
  original file must stay put or the editor shows media offline.

Captions are not produced here -- the `captions` skill handles them, reading
this trimmed output. It applies proper cue segmentation (line length, reading
speed, sentence-aware breaks) that a by-product SRT from this script did not,
and keeping it separate avoids leaving two competing caption files in the folder.

Originals are never modified, and nothing else is left behind: on exit the
output folder holds exactly these two files per video and nothing more.
The run summary (runtime before/after and what was removed) is printed to the
console rather than written to a file -- the user asked for an output folder
containing only files they load into an editor, so relay those numbers in your
report to them instead of saving them.
`--keep-intermediates` disables that pruning, and exists only for debugging --
never use it on a real run.

### Step 7: Verify before reporting -- not optional

Re-transcribe the finished cut, then verify against it:

```bash
python scripts/transcribe.py <output_dir>/<name>_trimmed.mp4 <out_transcript.json> --model small
python scripts/verify_output.py <output_dir>/<name>_trimmed.mp4 --original <video> \
    --check-transcript <out_transcript.json>
```

This fails loudly if the video opens on silence or any internal gap exceeds
150ms, and separately lists repeated phrases still present in the cut.

Re-transcribing the *output* is not busywork -- it is the only way to catch
restarts that were invisible in the original transcript (see signature 0 in
Step 5). Once the dead air around a hidden restart is removed, the duplication
becomes plain in a fresh transcript.

Repeated phrases are flagged for **review, not automatic cutting**: deliberate
parallelism looks identical to a restart in text ("you know what Tuesday's post
is for" / "Tuesday's post is for nothing" is rhetoric). Decide each one; if it
is a restart, add it to the speech cutlist and re-render.

Also compare the output transcript's word count against the original. A drop
beyond the words you intentionally cut means the edit is shaving real speech --
usually a consonant clipped at a cut boundary (an `s` or `f` sits low enough in
level to look like silence). If that happens, lower `--noise-db` toward -50 or
raise `--residual-ms`.

**Report the verifier's numbers, not your intended numbers.** If it fails, fix
it before telling the user anything is done -- a failing render described as
finished is worse than no render.

### Step 8: Report

Plain language: runtime before/after, what was removed, where the files are, and
the verification result. No raw JSON, no ffmpeg logs.

## Failure modes that have actually happened

These are not hypothetical. Each one shipped a broken edit to a real user, and
each is now designed out -- but only if the workflow above is followed as
written.

1. **Silence inferred from word-timestamp gaps.** Whisper stretches a single
   word's end timestamp across following silence -- the word "one" was reported
   spanning 29.48s to 34.12s, hiding ~4.4s of dead air *inside* one word. Word
   gaps saw 64s of silence in a clip that truly had 82s. **Always use
   `audio_silences`.**
2. **A "safety buffer" that ate the edit.** Shrinking every cut inward by 350ms
   per side meant any cut under 700ms vanished entirely: 23 of 35 planned cuts
   were silently dropped, and a silent sliver was left at the very start of the
   video. Padding now lives only in `plan_cuts.py`. **Never re-pad at render
   time.**
3. **Reporting intent as outcome.** The summary was computed from the requested
   cutlist rather than the applied cuts, so it announced "38s removed" when 16s
   was removed. **Numbers must come from measurement.**
4. **Concluding "this footage has no restarts."** A whole clip was declared
   clean after a skim; it actually contained at least five restarts, including a
   line abandoned mid-phrase. Skimming for disfluencies does not work -- **walk
   the transcript looking for the five signatures in Step 5.**
5. **Never checking the output.** Every bug above was trivially visible in the
   finished file and none were caught, because nothing measured it. **Step 7
   exists for this reason.**
6. **Assuming the transcript shows all the speech.** A single 4.64s token
   transcribed as `"one"` was concealing a full restarted sentence. Reading the
   transcript could never have found it; it only surfaced after tightening the
   silence and re-transcribing the output. **Treat `suspect_words` as unread
   audio, and always re-transcribe the finished cut.**
7. **Cutting too close to speech.** At `--noise-db -40` the `s` was shaved off
   "likes", because sibilants are quiet enough to register as silence. Hence
   `-45` plus a 90ms residual. Tight is the goal; clipped words are not.

8. **Cluttering the output folder.** A run left `transcript.json`,
   `cutlist_silence.json` and `cutlist_speech.json` next to the deliverables and
   the user had to ask which files mattered. Intermediates go in a scratch dir
   (Step 0); the output folder is for finished work only.

9. **The rendered video and the XML disagreeing on length.** ffmpeg's trim keeps
   whole frames, so each kept segment can run up to a frame long; across 135
   segments that accumulated to a full second, while the XML rounded to nearest
   and sometimes rounded down. One edit, three durations: exact sum 163.310s,
   XML 163.100s, rendered 164.088s. It matters because `slide-cutout` reads cue
   times from the rendered video and places them on a timeline built from the
   XML -- two disagreeing clocks make slides drift, and the drift grows through
   the video. Boundaries are snapped to the frame grid before rendering so both
   paths work from identical times. **Never let the render and the XML compute
   their own times independently.**

The pattern connecting all of them: the pipeline reported what it *meant* to do
instead of what it *did*, and trusted the transcript as though it were the
audio. Measure the artifact, then speak.
