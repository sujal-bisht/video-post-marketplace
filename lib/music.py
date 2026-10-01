"""Background music: pick a track that fits the video, and lay it under the voice.

THE LIBRARY
-----------
Tracks live in folders, not in code: the starter set shipped with the plugin
(lib/music/, public-domain tracks only) plus any folder the user points the
plugin at (brand.py set --music-folder). Nothing is typed in by hand. The first
time a track is seen it is measured -- length, loudness, tempo, energy,
brightness, where the music actually starts -- and given a mood from those
numbers. The results are cached in ~/.video-post/music_library.json, keyed by
file, so a library of a hundred tracks is measured once. A mood that the
numbers get wrong can be corrected in that file and the correction sticks.

THE MIX
-------
Music is for the vibe, never the message. The voice is at -14 LUFS (the sound
polish puts it there); the music sits around -25 LUFS while someone is
speaking and rises to about -20 LUFS where nobody is -- the opening seconds,
real pauses, the ending. That is 6-11 dB under the voice: present, never in
the way.

Ducking is driven by the transcript, not by a compressor listening to the
voice. The plugin already knows, to the word, when someone speaks, so the
music moves exactly when it should and stays still when it should. A
compressor reacts after the fact and pumps on every syllable.

The bed is built here as its own wav, aligned to the timeline: the editor
timeline gets it on its own audio tracks (move it, swap it, turn it down), and
the final video mixes it under the voice.
"""
import json
import os
import subprocess
import tempfile
import wave

import numpy as np

HOME = os.path.join(os.path.expanduser("~"), ".video-post")
CACHE = os.path.join(HOME, "music_library.json")
HISTORY = os.path.join(HOME, "music_history.json")
STARTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music")
EXTS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")

BED_LUFS = -20.0          # music where nobody is speaking
DUCK_DB = 5.0             # how far it drops under speech: -20 -> -25 LUFS
DUCK_DOWN_S = 0.25        # ramp down before a phrase starts
DUCK_UP_S = 0.8           # ramp up after it ends -- slower, so it never jumps
PAUSE_TO_RISE_S = 1.2     # only a pause this long lets the music come up
FADE_IN_S = 1.0
FADE_OUT_S = 2.5
LOOP_XFADE_S = 2.0
RATE = 48000

MOODS = ("calm", "warm", "cinematic", "upbeat", "moody")
MOOD_HELP = {
    "calm": "gentle, slow, unobtrusive -- teaching, explaining, reflective talk",
    "warm": "friendly, mid-tempo, positive -- personal stories, encouragement",
    "cinematic": "spacious, building, emotional -- big ideas, transformations, vision",
    "upbeat": "driving, energetic, bright -- tips, hype, fast-paced short-form",
    "moody": "dark, low, atmospheric -- confessions, hard truths, late-night tone",
}


# ------------------------------------------------------------------ analysis

def _decode(path, rate=22050, channels=1):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", str(channels),
                        "-ar", str(rate), "-f", "f32le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float64)


def _lufs(path, start=None, duration=None):
    import re
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    if start is not None:
        cmd += ["-ss", "%.3f" % start]
    if duration is not None:
        cmd += ["-t", "%.3f" % duration]
    cmd += ["-i", path, "-af", "ebur128", "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stderr
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    return float(m[-1]) if m else None


def analyze(path):
    """Measure one track. Pure numbers; the mood is derived from them."""
    rate = 22050
    x = _decode(path, rate)
    dur = len(x) / float(rate)
    hop = 512
    n = len(x) // hop
    frames = x[:n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1)) + 1e-9
    db = 20 * np.log10(rms)
    # Where the music really starts: the first moment within 15 dB of its
    # typical level. Many files open on a second or more of near-silence.
    typical = np.percentile(db, 60)
    start_idx = int(np.argmax(db > typical - 15))
    start = start_idx * hop / float(rate)

    # Tempo from the autocorrelation of the onset envelope. A raw maximum
    # always favours the shortest repeating pattern -- a calm piano piece read
    # as 172 BPM -- so lags are weighted toward the range people tap along to
    # (a log-normal prior around 110 BPM), the standard fix.
    win = 2048
    spec = np.abs(np.fft.rfft(np.lib.stride_tricks.sliding_window_view(
        x[: (len(x) // hop) * hop], win)[::hop] * np.hanning(win), axis=1))
    flux = np.maximum(0, np.diff(np.log1p(spec), axis=0)).sum(axis=1)
    flux = flux - flux.mean()
    fps = rate / float(hop)
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    lags = np.arange(int(fps * 60 / 200), int(fps * 60 / 50))
    bpms = 60.0 * fps / lags
    prior = np.exp(-0.5 * (np.log2(bpms / 110.0) / 0.9) ** 2)
    lag = lags[int(np.argmax(ac[lags] * prior))]
    bpm = 60.0 * fps / lag

    # Brightness: magnitude-weighted spectral centroid of the sounding frames.
    # (Weighted by power it is dominated by the bass and read 150-600 Hz for
    # everything.)
    freqs = np.fft.rfftfreq(win, 1.0 / rate)
    loud = db[:len(spec)] > np.percentile(db, 30)
    mag = spec[loud] if loud.any() else spec
    centroid = float(np.median((mag * freqs).sum(axis=1) / (mag.sum(axis=1) + 1e-12)))
    punch = float(np.percentile(flux + flux.mean(), 90) / (np.median(np.abs(flux)) + 1e-9))
    # Dynamics over the frames that actually sound: digital silence at the
    # start or end of a file read as a 160 dB range.
    sounding = db[db > -70]
    spread = float(np.percentile(sounding, 95) - np.percentile(sounding, 10)) if len(sounding) else 0.0
    # Energy: how loud the track is, frame by frame, relative to its own peak --
    # a flat, dense track is "driving"; a sparse one breathes.
    density = float(np.mean(sounding > np.percentile(sounding, 95) - 12)) if len(sounding) else 0.0

    lufs = _lufs(path)
    return {"duration": round(dur, 2), "start": round(start, 2), "lufs": lufs,
            "bpm": round(float(bpm), 1), "brightness_hz": round(centroid),
            "punch": round(punch, 2), "dynamics_db": round(spread, 1),
            "density": round(density, 2),
            "mood": _mood(bpm, centroid, density, spread), "mood_source": "measured guess"}


def _mood(bpm, centroid, density, spread):
    """A first guess at the mood from the measurements.

    Only a guess: numbers cannot hear a mood. The model labels each new track
    once from its title and these numbers (music_library.py label), and the
    user can correct any label; both stick.
    """
    if bpm >= 115 and density > 0.45 and centroid > 1500:
        return "upbeat"
    if centroid < 1200 and spread < 16:
        return "moody"
    if spread >= 18 and density < 0.4:
        return "cinematic"
    if bpm < 100 and density < 0.5:
        return "calm"
    return "warm"


# ------------------------------------------------------------------- library

def _folders(extra=()):
    out = []
    for f in [STARTER] + list(extra):
        if f and os.path.isdir(f) and os.path.abspath(f) not in out:
            out.append(os.path.abspath(f))
    return out


def library(extra_folders=(), quiet=False):
    """Every track in the starter set and the user's folders, measured.

    Returns a list of dicts (path, title, mood, duration, ...). New or changed
    files are measured now; known ones come from the cache.
    """
    try:
        with open(CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    tracks, changed = [], False
    for folder in _folders(extra_folders):
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith(EXTS):
                continue
            path = os.path.join(folder, name)
            st = os.stat(path)
            key = os.path.abspath(path)
            hit = cache.get(key)
            if not hit or hit.get("size") != st.st_size or hit.get("mtime") != int(st.st_mtime):
                if not quiet:
                    print("  measuring %s" % name, flush=True)
                info = analyze(path)
                info.update({"size": st.st_size, "mtime": int(st.st_mtime)})
                if hit and hit.get("mood_source") in ("user", "model"):
                    info["mood"], info["mood_source"] = hit["mood"], hit["mood_source"]
                cache[key] = hit = info
                changed = True
            tracks.append(dict(hit, path=key, title=_title(name),
                               starter=os.path.dirname(key) == os.path.abspath(STARTER)))
    if changed:
        os.makedirs(HOME, exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=1)
    return tracks


def _title(name):
    stem = os.path.splitext(name)[0]
    return stem.replace("_", " ").strip()


def set_mood(path, mood, source="user"):
    """Label a track's mood; it survives re-measuring. source: "user" or "model"."""
    if mood not in MOODS:
        raise SystemExit("Mood must be one of: %s" % ", ".join(MOODS))
    with open(CACHE, encoding="utf-8") as f:
        cache = json.load(f)
    key = os.path.abspath(path)
    if key not in cache:
        raise SystemExit("Not in the library yet: %s" % path)
    if cache[key].get("mood_source") == "user" and source != "user":
        return False                # never overrule the user
    cache[key]["mood"], cache[key]["mood_source"] = mood, source
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=1)
    return True


def pick(mood, duration, extra_folders=(), avoid_recent=3):
    """The best track for a video of this mood and length.

    Mood first. Then length: a track at least as long as the video needs no
    loop, so it wins over a shorter one. Then variety: the last few picks are
    skipped when anything else fits, so a batch of videos does not all share
    one song. The user's own tracks are preferred over the starter set.
    """
    tracks = library(extra_folders)
    if not tracks:
        return None
    try:
        recent = json.load(open(HISTORY, encoding="utf-8"))[-avoid_recent:]
    except Exception:
        recent = []

    def score(t):
        s = 0.0
        s += 10 if t["mood"] == mood else (4 if _near(t["mood"], mood) else 0)
        usable = t["duration"] - t["start"]
        s += 3 if usable >= duration else 3 * usable / max(duration, 1)
        s -= 5 if t["path"] in recent else 0
        s += 1 if not t["starter"] else 0
        return s

    best = max(tracks, key=score)
    return best


def remember(path):
    try:
        hist = json.load(open(HISTORY, encoding="utf-8"))
    except Exception:
        hist = []
    hist = (hist + [path])[-20:]
    os.makedirs(HOME, exist_ok=True)
    with open(HISTORY, "w", encoding="utf-8") as f:
        json.dump(hist, f)


_NEIGHBOURS = {"calm": ("warm", "cinematic"), "warm": ("calm", "upbeat"),
               "cinematic": ("calm", "moody"), "upbeat": ("warm",),
               "moody": ("cinematic", "calm")}


def _near(a, b):
    return a in _NEIGHBOURS.get(b, ())


# ----------------------------------------------------------------------- bed

def speech_regions(words, pause=PAUSE_TO_RISE_S):
    """Stretches where someone is speaking, merged across short gaps."""
    regions = []
    for w in words:
        if "start" not in w or "end" not in w:
            continue
        if regions and w["start"] - regions[-1][1] < pause:
            regions[-1][1] = max(regions[-1][1], w["end"])
        else:
            regions.append([w["start"], w["end"]])
    return regions


def build_bed(track, duration, words, out_wav):
    """The music, cut to `duration`, levelled and ducked under the speech.

    Returns a report. Written as 48 kHz stereo 16-bit, sample-aligned to the
    timeline from 0.
    """
    path, start = track["path"], track.get("start", 0.0)
    music = _decode(path, RATE, 2).reshape(-1, 2)
    music = music[int(start * RATE):]
    need = int(round(duration * RATE))

    # Loop with a crossfade when the track is shorter than the video.
    loops = 0
    if len(music) < need:
        xf = int(LOOP_XFADE_S * RATE)
        body = music.copy()
        ramp = np.linspace(0, 1, xf)[:, None]
        while len(music) < need and len(body) > xf * 2:
            head = body[:xf] * ramp
            tail = music[-xf:] * (1 - ramp)
            music = np.concatenate([music[:-xf], tail + head, body[xf:]])
            loops += 1
    music = music[:need]
    if len(music) < need:
        music = np.concatenate([music, np.zeros((need - len(music), 2))])

    # Level: bring the part we use to BED_LUFS (measured, not guessed).
    fd, tmp = tempfile.mkstemp(prefix="vp-music-", suffix=".wav")
    os.close(fd)
    try:
        _write(tmp, music)
        measured = _lufs(tmp)
    finally:
        os.remove(tmp)
    gain_db = (BED_LUFS - measured) if measured is not None else 0.0
    music *= 10 ** (gain_db / 20.0)

    # Ducking envelope from the transcript, in dB, then smoothed ramps.
    t = np.arange(need) / float(RATE)
    env = np.zeros(need)
    for a, b in speech_regions(words):
        a0, b0 = max(0.0, a - DUCK_DOWN_S), min(duration, b)
        i0, i1 = int(a0 * RATE), int(b0 * RATE)
        env[i0:i1] = -DUCK_DB
    env = _smooth_steps(env, int(DUCK_DOWN_S * RATE), int(DUCK_UP_S * RATE))
    # Fades.
    fi, fo = int(FADE_IN_S * RATE), int(FADE_OUT_S * RATE)
    fade = np.ones(need)
    fade[:fi] = np.linspace(0, 1, min(fi, need))[:len(fade[:fi])]
    if fo < need:
        fade[-fo:] = np.linspace(1, 0, fo)
    music *= (10 ** (env / 20.0) * fade)[:, None]
    peak = float(np.abs(music).max()) if len(music) else 0.0
    if peak > 0.89:                       # never clip; keep headroom under the voice
        music *= 0.89 / peak
    _write(out_wav, music)
    return {"track": track["title"], "mood": track["mood"], "loops": loops,
            "level_lufs_unducked": BED_LUFS, "ducked_lufs": BED_LUFS - DUCK_DB,
            "gain_applied_db": round(gain_db, 1)}


def _smooth_steps(env, down, up):
    """Turn a stepped dB envelope into ramps: quick-ish down, slow up."""
    out = env.copy()
    # downward edges: ramp over `down` samples ending at the edge
    d = np.diff(env, prepend=env[0])
    for i in np.nonzero(d < 0)[0]:
        a = max(0, i - down)
        out[a:i] = np.minimum(out[a:i], np.linspace(env[a], env[i], i - a))
    for i in np.nonzero(d > 0)[0]:
        b = min(len(env), i + up)
        out[i:b] = np.minimum(out[i:b], np.linspace(env[i - 1], env[i], b - i))
    return out


def _write(path, stereo):
    data = np.clip(stereo, -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


# ------------------------------------------------------------- the timeline

def add_music_tracks(xml_path, music_wav, fps_timebase, ntsc, frames):
    """Put the music bed on its own two audio tracks (left, right) of the timeline.

    One clip per channel, like the camera audio: FCP7 XML describes a stereo
    source as a clipitem per channel. Re-running replaces it.
    """
    import re
    text = open(xml_path, encoding="utf-8").read()
    text = re.sub(r"\s*<track><!-- vp-music -->.*?</track>", "", text, flags=re.S)
    from fcp7xml import _pathurl
    name = os.path.basename(music_wav)
    rate = "<rate><timebase>%d</timebase><ntsc>%s</ntsc></rate>" % (fps_timebase, ntsc)
    file_full = ('<file id="file-music"><name>%s</name><pathurl>%s</pathurl>%s'
                 "<duration>%d</duration><media><audio><samplecharacteristics>"
                 "<depth>16</depth><samplerate>%d</samplerate></samplecharacteristics>"
                 "<channelcount>2</channelcount></audio></media></file>"
                 % (name, _pathurl(music_wav), rate, frames, RATE))
    tracks = []
    for ch in (1, 2):
        f = file_full if ch == 1 else '<file id="file-music"/>'
        tracks.append(
            "<track><!-- vp-music -->"
            '<clipitem id="music-%d"><name>%s</name>%s<start>0</start><end>%d</end>'
            "<in>0</in><out>%d</out><enabled>TRUE</enabled>%s"
            "<sourcetrack><mediatype>audio</mediatype><trackindex>%d</trackindex></sourcetrack>"
            "</clipitem></track>" % (ch, name, rate, frames, frames, f, ch))
    # The sequence's own </audio> is the last one before the sequence's </media>,
    # which is the last </media> in the file; every clip's file description
    # nests its own <media><audio> earlier on.
    seq_media_end = text.rindex("</media>")
    close = text.rindex("</audio>", 0, seq_media_end)
    text = text[:close] + "".join(tracks) + text[close:]
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(text)
