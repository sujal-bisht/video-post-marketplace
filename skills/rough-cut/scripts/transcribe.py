"""
Transcribe a video/audio file with word-level timestamps using a local Whisper
model (faster-whisper / ctranslate2 backend -- no cloud calls, nothing leaves
this machine).

Usage:
    python transcribe.py <input_media> <output_json> [--model tiny|base|small|medium]

Output JSON schema:
{
  "duration": 28.04,
  "words": [
    {"word": "Hello", "start": 2.01, "end": 2.34, "prob": 0.98},
    ...
  ],
  "silences": [
    {"start": 0.0, "end": 2.01, "duration": 2.01, "position": "leading"},
    {"start": 5.10, "end": 6.30, "duration": 1.20, "position": "mid"},
    {"start": 26.04, "end": 28.04, "duration": 2.00, "position": "trailing"}
  ]
}

"silences" are pure gaps between consecutive recognized words (plus lead-in
and trail-out), computed directly from word timestamps -- this is what makes
dead-air detection deterministic and independent of whatever cut judgment
calls get layered on top later.
"""
import sys
import re
import json
import argparse
import subprocess
import tempfile
import os


def register_nvidia_dll_dirs():
    """On Windows, pip-installed nvidia-cublas-cu12 / nvidia-cudnn-cu12 drop their
    DLLs inside site-packages, but Windows only searches PATH and a fixed set of
    system directories for DLLs -- it won't look inside site-packages on its own.
    ctranslate2 (faster-whisper's backend) then fails to find cublas/cudnn at
    inference time even though the packages are correctly installed.

    os.add_dll_directory() looks like the "proper" fix but does NOT help here --
    ctranslate2's loader doesn't opt into that search path, only the classic PATH
    environment variable (confirmed empirically). So prepend to PATH directly.
    """
    if os.name != "nt":
        return
    try:
        import importlib.util
        bin_dirs = []
        for pkg in ("nvidia.cublas", "nvidia.cudnn"):
            spec = importlib.util.find_spec(pkg)
            if spec and spec.submodule_search_locations:
                bin_dir = os.path.join(list(spec.submodule_search_locations)[0], "bin")
                if os.path.isdir(bin_dir):
                    bin_dirs.append(bin_dir)
        if bin_dirs:
            os.environ["PATH"] = os.pathsep.join(bin_dirs) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass  # best-effort -- CPU fallback still works if this doesn't pan out


register_nvidia_dll_dirs()


def probe_duration(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path
    ])
    return float(out.strip())


def detect_audio_silences(wav_path, duration, noise_db, min_silence):
    """Find real dead air by measuring audio level, via ffmpeg's silencedetect.

    This is the authoritative source of silence and it exists because inferring
    silence from gaps BETWEEN word timestamps badly under-detects: Whisper
    routinely stretches a single word's end timestamp across following silence
    (a real example: the word "one" reported as spanning 29.48s -> 34.12s,
    hiding ~4.4s of dead air *inside* one word). Word gaps cannot see that;
    audio levels can.
    """
    proc = subprocess.run(
        ["ffmpeg", "-i", wav_path, "-af",
         f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    regions = []
    start = None
    for line in proc.stderr.splitlines():
        m = re.search(r"silence_start:\s*(-?[0-9.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m and start is not None:
            regions.append((start, float(m.group(1))))
            start = None
    if start is not None:
        regions.append((start, duration))

    out = []
    for s, e in regions:
        if e <= s:
            continue
        position = "leading" if s <= 0.15 else ("trailing" if e >= duration - 0.15 else "mid")
        out.append({
            "start": round(s, 3), "end": round(e, 3),
            "duration": round(e - s, 3), "position": position,
        })
    return out


def extract_audio(video_path, wav_path):
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", wav_path
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def transcribe_with_fallback(model_size, wav_path):
    """GPU load can succeed but inference can still fail (e.g. missing CUDA/cuBLAS
    DLLs on the host). Only discovered once the segment generator is actually
    consumed, so retry on CPU if that happens."""
    from faster_whisper import WhisperModel
    tried_gpu = False
    try:
        model = WhisperModel(model_size, device="cuda", compute_type="float16")
        tried_gpu = True
        segments, _info = model.transcribe(wav_path, word_timestamps=True, vad_filter=False)
        return list(segments)
    except Exception as e:
        if not tried_gpu:
            raise
        print(f"GPU path failed ({e}); falling back to CPU.", file=sys.stderr)
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(wav_path, word_timestamps=True, vad_filter=False)
        return list(segments)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_media")
    ap.add_argument("output_json")
    ap.add_argument("--model", default="small",
                    help="Whisper model size: tiny, base, small, medium, large-v3. "
                         "small is a good default speed/accuracy tradeoff for talking-head footage.")
    ap.add_argument("--noise-db", type=float, default=-45.0,
                    help="Level below which audio counts as silence. -45 is calibrated to protect "
                         "sibilants and fricatives (an 's' or 'f' is quiet enough that -40 clipped "
                         "the 's' off 'likes' on real footage). -30 eats consonants outright; -50 "
                         "starts missing quiet room tone.")
    ap.add_argument("--min-silence", type=float, default=0.12,
                    help="Shortest silence worth recording, in seconds.")
    args = ap.parse_args()

    duration = probe_duration(args.input_media)

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "audio.wav")
        extract_audio(args.input_media, wav_path)

        audio_silences = detect_audio_silences(wav_path, duration, args.noise_db, args.min_silence)

        segments = transcribe_with_fallback(args.model, wav_path)

        words = []
        for seg in segments:
            for w in seg.words:
                words.append({
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "prob": round(w.probability, 3),
                })

    # Gaps between word timestamps. Kept only as a secondary hint for reading the
    # transcript -- NOT for silence removal, because Whisper hides silence inside
    # inflated word durations. plan_cuts.py uses audio_silences instead.
    silences = []
    if words:
        if words[0]["start"] > 0:
            silences.append({
                "start": 0.0, "end": words[0]["start"],
                "duration": round(words[0]["start"], 3), "position": "leading"
            })
        for a, b in zip(words, words[1:]):
            gap = b["start"] - a["end"]
            if gap > 0.05:  # ignore sub-50ms noise
                silences.append({
                    "start": round(a["end"], 3), "end": round(b["start"], 3),
                    "duration": round(gap, 3), "position": "mid"
                })
        last_end = words[-1]["end"]
        if duration - last_end > 0.05:
            silences.append({
                "start": round(last_end, 3), "end": round(duration, 3),
                "duration": round(duration - last_end, 3), "position": "trailing"
            })
    else:
        silences.append({"start": 0.0, "end": duration, "duration": duration, "position": "leading"})

    # Any single "word" lasting over a second is not a word. Whisper collapses
    # material it can't align into one stretched token, and that token can hide
    # dead air OR real speech -- on real footage a 4.64s "one" turned out to
    # contain an entire restarted sentence. Flag them so they get inspected
    # rather than trusted.
    suspect_words = [
        {"word": w["word"], "start": w["start"], "end": w["end"],
         "duration": round(w["end"] - w["start"], 3), "prob": w["prob"]}
        for w in words if (w["end"] - w["start"]) > 1.0
    ]

    out = {
        "duration": round(duration, 3),
        "words": words,
        "audio_silences": audio_silences,
        "word_gaps": silences,
        "suspect_words": suspect_words,
    }
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    total_silence = sum(s["duration"] for s in audio_silences)
    print(f"Transcribed {len(words)} words, duration {duration:.2f}s")
    print(f"Real dead air: {len(audio_silences)} regions, {total_silence:.1f}s "
          f"({100 * total_silence / duration:.0f}% of runtime)")
    print(f"(word-gap estimate for comparison: {sum(s['duration'] for s in silences):.1f}s -- "
          f"this is why silence removal must not rely on word gaps)")
    if suspect_words:
        print(f"\n{len(suspect_words)} suspect word(s) lasting over 1s -- these often hide dead air "
              f"or an entire restarted sentence. Inspect each before planning speech cuts:")
        for w in suspect_words:
            print(f"    {w['word']!r:16} {w['start']:7.2f} -> {w['end']:7.2f}  ({w['duration']:.2f}s)")
    print(f"\nWrote {args.output_json}")


if __name__ == "__main__":
    main()
