# Video Post-Production

Automated post-production for talking-head course video. Everything runs locally
— transcription included — so footage never leaves the machine.

## Layout

```
video-post/
├── .claude-plugin/plugin.json
├── lib/
│   └── fcp7xml.py        shared FCP7 XML (XMEML) timeline writer
├── tools/
│   └── build_skills.py   builds standalone .skill files for delivery
├── dist/                 built .skill files (generated)
└── skills/
    ├── rough-cut/        dead air, filler words, botched takes
    └── slide-cutout/     slides on screen, speaker in a corner circle
```

## Skills

**rough-cut** — turns raw footage into a tight first pass. Measures real silence
from audio levels, compresses every pause, finds false starts and self-corrections
by reading the transcript, and verifies the finished file rather than trusting
that the edit worked. Delivers a trimmed video plus an editable timeline.

**slide-cutout** — puts slides on screen with the speaker as a circular corner
cutout. Slide timing comes from spoken cue phrases matched against the
transcript, so no timestamps are typed by hand. Delivers camera, slides and
cutout as separate tracks of ordinary clips, so timings stay adjustable.

## Why `lib/` exists

Both rough-cut and slide-cutout emit FCP7 XML. Written
twice, the copies drift — a fix lands in one and is forgotten in the other. One
implementation lives in `lib/fcp7xml.py` and every skill imports it.

Each skill looks for the module in **two** places -- alongside its own scripts,
and at the plugin's `lib/` -- so the identical source runs in either layout with
nothing generated or rewritten.

## Shipping a skill on its own

Uploading a `.skill` file into Claude is much easier for a non-technical user
than installing a plugin, so the plugin is the source of truth for development
and standalone packages are built from it:

```bash
python tools/build_skills.py            # all skills -> dist/
python tools/build_skills.py rough-cut  # just one
```

It stages a copy of the skill, drops `lib/*.py` in beside the scripts, and
packages that. The result is self-contained: it runs with no plugin installed.
Verified by extracting a built `.skill` outside the plugin and rendering with it
-- the video came out byte-identical and the XML identical to the plugin build.

**Build from the plugin, never edit `dist/`.** A `.skill` is a snapshot; the
plugin is the original.

## Why FCP7 XML and not FCPXML

Premiere Pro imports FCP7 XML (XMEML) and not FCPXML — Adobe never added Final
Cut Pro X's format. DaVinci Resolve reads FCP7 XML too. One format therefore
serves both editors, so there is no reason to ship two interchange files.

## Requirements

- `ffmpeg` and `ffprobe` on PATH
- `pip install faster-whisper`
- Optional GPU acceleration: `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`
  (falls back to CPU automatically)

## A note on changing the timeline writer

`lib/fcp7xml.py` does all its arithmetic in whole frames. That is not stylistic:
accumulating timeline positions in float seconds and rounding at the end drifted
a frame per clip on a 13-clip test and finished a frame short of the rendered
video. `contiguous_clips()` exists to keep back-to-back edits frame-exact. If you
touch this file, re-run a render both before and after and compare the resulting
timelines clip by clip — a one-frame slip is invisible in the code and obvious on
a timeline.
