"""Make the voice sound clean and sit at the loudness the platforms play at.

Runs on the cut audio (<name>_audio.wav), so the editor timeline and the final
video both get the polished sound, and nothing is asked: every step either
measures what the recording needs or does nothing.

THE CHAIN, AND WHY EACH STEP IS GENTLE
--------------------------------------
1. High-pass at 80 Hz. A speaking voice has almost nothing below 80 Hz; mains
   hum, desk thumps, traffic and air-conditioning rumble live there. Cutting it
   is free clarity and cannot be heard on the voice.
2. Noise reduction -- only when the room is actually noisy. The noise floor is
   MEASURED from the quietest moments of the recording. A quiet room is left
   alone, because denoising clean audio is all cost: the familiar watery,
   robotic voice is what over-reduction sounds like. A noisy one is reduced by a
   moderate, fixed amount, never "as much as possible".
3. Gentle compression (2.5:1 above -21 dB). Brings quiet words and loud words
   closer, so nothing gets lost under a phone's speaker, without the squashed,
   pumping sound of heavy compression.
4. Loudness to -14 LUFS, true peak -1.5 dBTP, measured in a first pass and
   applied in a second (two-pass loudnorm, linear). -14 is what YouTube,
   Instagram, TikTok and Spotify normalise playback to; a video louder than
   that gets turned down, a quieter one sounds weak next to everything around
   it. Linear mode applies one steady gain rather than riding the level.

None of these change timing. That is verified after every run: same number of
samples, and the polished sound lines up with the original to the sample.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import wave

import numpy as np

TARGET_LUFS = -14.0
TARGET_TP = -1.5
TARGET_LRA = 11.0
HIGHPASS_HZ = 80
AUDIBLE_NOISE_DB = -58.0   # room noise this loud AFTER the loudness lift is worth reducing
MIN_REDUCTION_DB = 8.0
MAX_REDUCTION_DB = 14.0    # past this a voice starts to sound processed


def _read(path):
    with wave.open(path, "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2" if width == 2 else "<i4")
    data = data.reshape(-1, ch).astype(np.float64) / (32768.0 if width == 2 else 2147483648.0)
    return data, rate


def noise_floor_db(path, window=0.05):
    """Level of the quietest moments, in dBFS: the room the voice sits in.

    The 10th-percentile of 50 ms windows. In a cut with the pauses squeezed out
    there are still breaths and the 90 ms left between phrases, which is
    enough. The quietest few windows can be digital silence at a cut, so the
    percentile, not the minimum.
    """
    data, rate = _read(path)
    mono = data.mean(axis=1)
    n = int(rate * window)
    if len(mono) < n * 20:
        return None
    frames = mono[:len(mono) // n * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    rms = rms[rms > 1e-7]                     # ignore exact digital silence
    if not len(rms):
        return None
    return float(20 * np.log10(np.percentile(rms, 10)))


def speech_level_db(path, window=0.05):
    """Level of the voice itself: the loud half of the windows."""
    data, rate = _read(path)
    mono = data.mean(axis=1)
    n = int(rate * window)
    frames = mono[:len(mono) // n * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    return float(20 * np.log10(max(np.percentile(rms, 75), 1e-9)))


def loudness(path):
    """Integrated loudness (LUFS), true peak (dBTP) and range (LU), as measured."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
                        "loudnorm=I=%.1f:TP=%.1f:LRA=%.1f:print_format=json"
                        % (TARGET_LUFS, TARGET_TP, TARGET_LRA), "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        raise SystemExit("Could not measure loudness:\n" + r.stderr[-800:])
    return json.loads(m.group(0))


def room_noise_db(media, silences, max_regions=40):
    """The room's own noise, measured in the recording's real pauses, in dBFS.

    Measured on the RAW recording, in the silences transcription already found.
    The cut audio is the wrong place: with the pauses squeezed out, its
    quietest moments are breaths, and on a quiet DJI recording that read as
    -56 dB of "noise" where the room was really at -62 -- enough to send clean
    audio through noise reduction it did not need.
    """
    regions = sorted((s for s in silences or [] if s.get("duration", 0) >= 0.5),
                     key=lambda s: -s["duration"])[:max_regions]
    if not regions:
        return None
    fd, tmp = tempfile.mkstemp(prefix="vp-room-", suffix=".wav")
    os.close(fd)
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", media, "-vn", "-ac", "1",
                        "-ar", "16000", "-c:a", "pcm_s16le", tmp], check=True)
        data, rate = _read(tmp)
    finally:
        os.remove(tmp)
    mono = data.mean(axis=1)
    levels = []
    for s in regions:
        a, b = int((s["start"] + 0.1) * rate), int((s["end"] - 0.1) * rate)
        if b - a > rate * 0.2:
            seg = mono[a:b]
            levels.append(20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-12))
    return float(np.median(levels)) if levels else None


def plan(path, room_db=None):
    """Decide the chain for this recording. Returns (filter string, report dict).

    Noise reduction is decided by what the viewer will HEAR: the room noise as
    it will sit once the loudness step has lifted everything to -14 LUFS. A
    quiet recording that gets turned up a lot can need it; a louder one in the
    same room may not.
    """
    floor = room_db if room_db is not None else noise_floor_db(path)
    lufs = float(loudness(path)["input_i"])
    lift = TARGET_LUFS - lufs
    heard = None if floor is None else floor + lift
    report = {"room_noise_db": None if floor is None else round(floor, 1),
              "measured_in": "the recording's pauses" if room_db is not None else "the cut",
              "before_lufs": round(lufs, 2),
              "noise_after_lift_db": None if heard is None else round(heard, 1)}
    steps = ["highpass=f=%d:poles=2" % HIGHPASS_HZ]
    if heard is not None and heard > AUDIBLE_NOISE_DB:
        # The further above audible, the more is taken out -- up to a ceiling,
        # because a voice that sounds processed is worse than a little hiss.
        nr = min(MAX_REDUCTION_DB, max(MIN_REDUCTION_DB, heard - AUDIBLE_NOISE_DB + 6.0))
        steps.append("afftdn=nr=%.1f:nf=%.1f:tn=1" % (nr, max(-80.0, min(-20.0, floor))))
        report["noise_reduction_db"] = round(nr, 1)
    else:
        report["noise_reduction_db"] = 0.0
    # Compression only on a clean recording. It turns the loud voice down and
    # the loudness step then lifts everything back up -- background included.
    # On a noisy phone voice note that narrowed the gap between voice and
    # background from 19.5 dB to 12.1 dB: the "polished" version was noisier.
    if report["noise_reduction_db"] == 0.0:
        steps.append("acompressor=threshold=-21dB:ratio=2.5:attack=15:release=200:makeup=1")
        report["compression"] = True
    else:
        report["compression"] = False
    return ",".join(steps), report


def polish(in_wav, out_wav, room_db=None):
    """Polish in_wav into out_wav (may be the same path). Returns the report.

    `room_db`: the room noise measured in the raw recording (room_noise_db).
    """
    chain, report = plan(in_wav, room_db)
    with wave.open(in_wav, "rb") as w:
        rate, channels, frames_in = w.getframerate(), w.getnchannels(), w.getnframes()
    work = tempfile.mkdtemp(prefix="vp-audio-polish-")
    try:
        stage1 = os.path.join(work, "stage1.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", in_wav, "-af", chain,
                        "-ar", str(rate), "-c:a", "pcm_s16le", stage1], check=True)
        m = loudness(stage1)
        second = ("loudnorm=I=%.1f:TP=%.1f:LRA=%.1f:measured_I=%s:measured_TP=%s:"
                  "measured_LRA=%s:measured_thresh=%s:offset=%s:linear=true"
                  % (TARGET_LUFS, TARGET_TP, TARGET_LRA, m["input_i"], m["input_tp"],
                     m["input_lra"], m["input_thresh"], m["target_offset"]))
        out_tmp = os.path.join(work, "out.wav")
        # loudnorm works internally at 192 kHz; bring it back to the source rate,
        # and keep the exact sample count so nothing can drift against the picture.
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", stage1, "-af",
                        second + ",aresample=%d" % rate, "-ar", str(rate),
                        "-ac", str(channels), "-c:a", "pcm_s16le", out_tmp], check=True)
        # The noise reducer works in FFT blocks and delays the sound by about
        # a block: 23-25 ms, measured, on 48 kHz audio. Lip sync starts to feel
        # wrong around 40 ms, and this would stack with anything else, so the
        # delay is measured on this file and taken out, to the sample.
        # Only a real delay is corrected: below 5 ms the envelope estimate is
        # measuring the filters' phase, not a delay, and 5 ms is far below
        # anything a viewer could notice anyway.
        lag = alignment(in_wav, out_tmp)
        if abs(lag) < rate * 0.005:
            lag = 0
        if lag:
            _shift(out_tmp, lag)
        _fit_length(out_tmp, frames_in)
        after = loudness(out_tmp)
        report.update({"after_lufs": float(after["input_i"]),
                       "after_true_peak": float(after["input_tp"]),
                       "delay_corrected_samples": -lag,
                       "offset_samples": alignment(in_wav, out_tmp)})
        shutil.move(out_tmp, out_wav)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return report


def _shift(path, lag):
    """Move the audio by `lag` samples: negative = it was late, pull it earlier."""
    with wave.open(path, "rb") as w:
        params, data = w.getparams(), w.readframes(w.getnframes())
    width = params.sampwidth * params.nchannels
    if lag < 0:
        data = data[-lag * width:]
    else:
        data = b"\x00" * (lag * width) + data
    with wave.open(path, "wb") as w:
        w.setparams(params)
        w.writeframes(data)


def _fit_length(path, frames):
    """Make the file exactly `frames` samples long (pad with silence or trim)."""
    with wave.open(path, "rb") as w:
        params, data = w.getparams(), w.readframes(w.getnframes())
        have = w.getnframes()
    width = params.sampwidth * params.nchannels
    if have == frames:
        return
    data = data[:frames * width] if have > frames else data + b"\x00" * ((frames - have) * width)
    with wave.open(path, "wb") as w:
        w.setparams(params)
        w.writeframes(data)


def alignment(a, b, seconds=30.0):
    """Offset in samples between two versions of the same audio (0 = aligned).

    Measured on the loudness envelope, which survives filtering, over the first
    stretch of speech.
    """
    da, rate = _read(a)
    db, _ = _read(b)
    n = int(min(len(da), len(db), rate * seconds))
    x, y = np.abs(da[:n].mean(axis=1)), np.abs(db[:n].mean(axis=1))
    hop = max(1, rate // 4000)                 # 0.25 ms resolution
    x, y = x[::hop], y[::hop]
    k = 64
    x = np.convolve(x, np.ones(k) / k, "same")
    y = np.convolve(y, np.ones(k) / k, "same")
    best, lag_best = None, 0
    for lag in range(-200, 201):              # +-50 ms
        if lag >= 0:
            c = np.corrcoef(x[lag:], y[:len(y) - lag])[0, 1]
        else:
            c = np.corrcoef(x[:lag], y[-lag:])[0, 1]
        if best is None or c > best:
            best, lag_best = c, lag
    return int(lag_best * hop)
