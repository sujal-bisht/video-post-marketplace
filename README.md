# Video Post-Production for Claude Code

Automated post-production for talking-head course video. Everything runs on your
own machine — your footage is never uploaded anywhere.

Two skills:

- **rough-cut** — removes dead air, filler words, false starts and botched takes
  from raw footage
- **slide-cutout** — puts your slides on screen with you in a circular corner
  cutout

Both hand you an **editable timeline** for Premiere Pro or DaVinci Resolve, not a
finished video, so you keep the final say on every cut.

---

## Setup (once, about fifteen minutes)

### 1. Install the two tools the skills use

These do the actual audio and video work. The skills can't carry them, so they
install separately.

**Windows:**

```bash
winget install ffmpeg
pip install faster-whisper
```

**Mac:**

```bash
brew install ffmpeg
pip install faster-whisper
```

**Got an NVIDIA graphics card?** This makes transcription several times faster.
Skip it if you don't — everything still works, just slower.

```bash
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

### 2. Check it worked

```bash
ffmpeg -version
python -c "import faster_whisper; print('ready')"
```

Both should print something rather than an error. If `ffmpeg` isn't found after
installing, close and reopen your terminal so it picks up the new PATH.

### 3. Install the plugin

In an interactive Claude Code terminal session (run `claude`), then:

```
/plugin marketplace add <your-github-username>/video-post-marketplace
/plugin install video-post@video-post-marketplace
```

The `/plugin` commands need a real terminal session — they won't work from the
desktop app's Code tab.

---

## Using it

You don't run the plugin; you just say what you want and the right skill starts.

### Trim raw footage

Put your raw videos in a folder, then:

> Trim these videos — they're in C:\Videos\Lesson-Batch-1

It lists what it found, asks whether to do all of them or one, then for each
video removes the dead air and the fumbled takes. You get back:

- `<name>_trimmed.mp4` — the cut video, ready to use as-is
- `<name>.xml` — the same cut as separate clips, to import if you'd rather
  adjust the cuts yourself

Import the `.xml` in Premiere: `File > Import`. In Resolve:
`File > Import > Timeline > Import AAF, EDL, XML`.

**Keep the original file where it was** — the timeline points at it, so moving
it makes the media go offline.

### Add slides with you in the corner

Export your deck to images first: in PowerPoint, `Save As → PNG` gives you one
correctly-numbered image per slide, into a folder.

Then just describe where each slide goes, in your own words:

> Add my slides to lesson-03. They're in C:\Videos\Lesson-03-Slides.
> Slide 1 starts when I say "the first framework", slide 2 at "now the second
> piece", slide 3 at "and here's the last one". Go back to full frame when I say
> "so that's the theory".

You never type a timestamp. It finds those phrases in what you actually said and
places the slides there. You get:

- `<name>_slides.xml` — the timeline to import
- `<name>_media/` — the cutout overlays and slide copies it uses

**Keep the `_media` folder next to the `.xml`.** On the timeline you'll find your
camera on the bottom track, slides above it, and your circular cutout on top —
all normal clips you can drag, shorten or delete.

### Captions

Premiere does these well on its own: `Text` panel → `Transcribe Sequence` →
`Create Captions`. Its transcript panel is the easiest place to fix any word it
mishears, so there's no skill here for it.

---

## Good to know

**Order matters.** Trim first, then add slides. The slide cues are matched
against the trimmed video, so anything you stumbled over is already gone.

**If a cue phrase can't be found**, it tells you and shows what the transcript
actually says nearby — usually enough to reword it. It won't guess and quietly
put a slide in the wrong place.

**If a phrase is one you say more than once**, it flags that too. Make the cue a
bit longer so it's unique.

**Overlay files are big** — roughly a gigabyte per two or three minutes of slide
time. That's normal for video with transparency. Delete the `_media` folder once
you've rendered your final video.

**Rendering takes real time.** A four-minute video is a few minutes of the
computer working. Batch of five, go make a coffee.

**If the pacing feels too rushed** after trimming, say so — the amount of pause
left between sentences is a single setting that's easy to loosen.

---

## Requirements

- Claude Code
- Python 3.9+
- ffmpeg
- Premiere Pro or DaVinci Resolve for the final polish (any editor works for the
  trimmed `.mp4`)
