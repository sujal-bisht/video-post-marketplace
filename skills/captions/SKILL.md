---
name: captions
description: >
  Puts captions in the user's brand font and colour onto a video that is
  ALREADY EDITED -- cut somewhere else, in CapCut or Premiere -- and changes
  nothing else about it. Four styles: one word at a time, a brand-colour chip,
  bold uppercase, or a phrase with the key word highlighted. Use only when the
  user says the video is already edited and wants captions on it. For raw
  footage, use edit-video instead, which cuts, zooms and captions together.
  Trigger on: "add captions to this edited video", "caption my finished video",
  "subtitles on this, it's already cut".
---

# Captions

Brand captions on a finished video, and nothing else.

## When this, and when edit-video

**This skill** is for a video that is already cut. Rough-cutting an edited video
would chop into edits the user already made on purpose.

**`edit-video`** is for raw footage, and it captions by default anyway. If you
are unsure whether the video is raw, ask one question: *"Is this already
edited, or straight from the camera?"*

## Workflow

### 1. The brand and the style

Exactly as in `edit-video` Step 1: confirm or ask for the brand font and colour,
show the four templates on their video, and ask which. Use its scripts:

```bash
python ../edit-video/scripts/brand.py show
python ../edit-video/scripts/brand.py set --font "Rethink Sans" --colour "#E90D41"
python ../edit-video/scripts/preview_templates.py <video>
python ../edit-video/scripts/brand.py template emphasis
```

The same rules hold: a font that cannot be found, or that cannot draw every
letter, stops and goes back to the user. Never substitute silently. And the
style question always comes with the picture: put the preview still in front
of the user in the conversation (in the Claude desktop app, `SendUserFile`
with `display: "render"`) before asking "1, 2, 3 or 4?". Never ask with the
names alone.

### 2. Transcribe

```bash
python ../rough-cut/scripts/transcribe.py <video> <scratch>/transcript.json --model small
```

Use `--model medium` when the speech is accented or mixes languages -- captions
put every mis-heard word on screen.

### 3. Caption

```bash
python scripts/caption_video.py <video> <scratch>/transcript.json <output_dir>
```

Writes `<name>_captioned.mp4` at 1080 on the short side, which is the ceiling
for short-form platforms. `--full-resolution` for a full-size file.

It reads ffmpeg's log to confirm the brand font was drawn and fails if it was
not. Report that result.
