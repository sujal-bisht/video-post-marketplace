"""Find the fumbles in a transcript: stutters, restarted phrases, filler sounds.

WHY THIS IS CODE, NOT ONLY JUDGMENT
-----------------------------------
Deciding restarts was left to the model reading the transcript, with a rule
that a word-for-word repeat is emphasis and stays. On Danny's DJI test that
kept "so, so nothing it writes..." -- the first "so" was a stumble, the rule
protected it, and the cut that removed the retaken line started one word too
late. A stutter is not a judgment call; it is a pattern. So the patterns are
found here, on every edit, before the cut AND on the transcript of the cut
itself, and the model only decides what code cannot (an abandoned phrase that
was reworded, a tangent).

WHAT COUNTS
-----------
stutter    the same word twice in a row -- "so so", "we we", "the, the" -- the
           first goes. Except words people double on purpose: "very very",
           "no no", "really really".
restart    a run of 2+ words said, then said again (after at most three
           abandoned words) -- "not just your, not just your ego", or a whole
           line retaken. The first attempt and the abandoned words go; the
           last attempt stays. Except a short complete sentence repeated
           exactly ("Watching you. Watching you."): that is an echo, kept.
filler     formless sounds: um, uh, erm, hmm.

Times come from the transcript; the cut runs from the start of the fumble to
the start of the take that replaces it, so nothing of the fumble survives the
junction. The silence cut then tightens the pause either side.
"""
import re

EMPHASIS = {"very", "really", "no", "never", "yes", "more", "much", "so-so", "bye",
            "ha", "go", "please", "now", "sehr", "nein", "ja", "nie"}
FILLERS = {"um", "uh", "uhm", "umm", "uhh", "erm", "er", "ah", "hmm", "mm", "äh", "ähm", "öhm"}
MAX_GAP_WORDS = 3          # abandoned words allowed between a phrase and its retake
MAX_PHRASE = 25            # longest phrase considered for a retake
ECHO_MAX_WORDS = 3         # an exact repeat this short, ending a sentence, is an echo


def _norm(word):
    return re.sub(r"[^\w'-]+", "", word.lower()).strip("-'")


def _ends_sentence(word):
    return word.rstrip()[-1:] in ".!?"


def find(words, lead=0.02):
    """Fumbles in `words` (dicts with word/start/end). Returns a list of cuts.

    Each cut: {"start", "end", "reason", "kind"}.
    """
    allw = [x for x in words if "start" in x and "end" in x and _norm(x["word"])]
    cuts = []
    # Filler sounds go on their own, and are invisible to the repeat search,
    # so "we um we pull" is still seen as "we we".
    for k, x in enumerate(allw):
        if _norm(x["word"]) in FILLERS:
            end = allw[k + 1]["start"] if k + 1 < len(allw) else x["end"]
            cuts.append({"start": round(max(0.0, x["start"] - lead), 3),
                         "end": round(min(end, x["end"] + 0.05), 3),
                         "reason": "filler: '%s'" % x["word"], "kind": "filler"})
    w = [x for x in allw if _norm(x["word"]) not in FILLERS]
    toks = [_norm(x["word"]) for x in w]
    n = len(w)
    i = 0
    while i < n:
        hit = None
        # restarts first: the longest phrase at i that is said again shortly after
        for k in range(min(MAX_PHRASE, (n - i) // 2), 1, -1):
            phrase = toks[i:i + k]
            for gap in range(0, MAX_GAP_WORDS + 1):
                j = i + k + gap
                if j + k > n:
                    break
                if toks[j:j + k] == phrase:
                    exact_echo = (gap == 0 and k <= ECHO_MAX_WORDS
                                  and _ends_sentence(w[i + k - 1]["word"])
                                  and _ends_sentence(w[j + k - 1]["word"]))
                    if not exact_echo:
                        hit = (i, j)
                    break
            if hit:
                break
        if hit:
            a, j = hit
            said = " ".join(x["word"] for x in w[a:j]).strip()
            cuts.append(_cut(w, a, j, lead, "restart",
                             "restart: '%s' said again -- the last take is kept" % said))
            i = j
            continue
        # stutter: one word twice
        if i + 1 < n and toks[i] == toks[i + 1] and toks[i] not in EMPHASIS:
            cuts.append(_cut(w, i, i + 1, lead, "stutter",
                             "stutter: '%s %s' -- the first goes" % (w[i]["word"], w[i + 1]["word"])))
        i += 1
    cuts.sort(key=lambda c: c["start"])
    return [c for c in cuts if c["end"] - c["start"] > 0.03]


def _cut(w, a, b, lead, kind, reason):
    """From the start of word a up to the start of word b (its replacement)."""
    start = w[a]["start"] - lead
    if a > 0:
        start = max(start, w[a - 1]["end"] + 0.01)
    return {"start": round(max(0.0, start), 3), "end": round(w[b]["start"] - 0.01, 3),
            "reason": reason, "kind": kind}


# ------------------------------------------------------------- dropped words

HALLUCINATIONS = {"thank you", "thanks for watching", "you", "bye", "thank you."}


def _levels(media, rate=16000, hop_s=0.02):
    import subprocess
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", media, "-vn", "-ac", "1", "-ar",
                          str(rate), "-f", "s16le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    hop = int(rate * hop_s)
    n = len(x) // hop
    rms = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1)) if n else np.zeros(0)
    return 20 * np.log10(rms + 1e-9), hop_s


def orphan_sounds(words, media, below_speech_db=15.0, min_len=0.08, join_gap=0.15):
    """Stretches as loud as speech that no transcribed word accounts for.

    Whisper drops words -- on the DJI test a whole spoken "So," before a
    retaken line was simply absent from the transcript, so no rule reading the
    transcript could see the stutter it made. Speech-level sound with no word
    on it is either such a dropped word or a noise (cough, lip click, bump).
    Returns [(start, end)] in seconds.
    """
    import numpy as np
    db, hop = _levels(media)
    if not len(db):
        return []
    limit = float(np.percentile(db, 80)) - below_speech_db
    loud = db >= limit
    runs, i, n = [], 0, len(db)
    while i < n:
        if loud[i]:
            j = i
            while j < n and loud[j]:
                j += 1
            runs.append([i * hop, j * hop])
            i = j
        else:
            i += 1
    merged = []
    for a, b in runs:
        if merged and a - merged[-1][1] < join_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    covered = [(w["start"] - 0.05, w["end"] + 0.05) for w in words if "start" in w and "end" in w]
    out = []
    for a, b in merged:
        if b - a < min_len:
            continue
        if any(s < b and e > a for s, e in covered):
            continue
        out.append((round(a, 3), round(b, 3)))
    return out


def recover(words, media, language=None, max_noise_s=0.8, model=None):
    """Fill in words Whisper dropped; find short noises with no words.

    Each orphan sound is transcribed on its own, with a little room either
    side. Words it yields are added to the transcript (sorted in), so the
    stutter and restart search sees them. A short orphan that yields no word is
    noise -- a cough, a click -- and becomes a cut; a longer one is left alone,
    because it may be laughter, which is never cut.

    Returns (words with the dropped ones restored, noise cuts).
    """
    import os
    import subprocess
    import tempfile
    orphans = orphan_sounds(words, media)
    if not orphans:
        return list(words), []
    if model is None:
        model = _model()
    found, noise = [], []
    for a, b in orphans:
        pad = 0.25
        fd, clip = tempfile.mkstemp(prefix="vp-orphan-", suffix=".wav")
        os.close(fd)
        try:
            start = max(0.0, a - pad)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % start, "-t",
                            "%.3f" % (b - a + 2 * pad), "-i", media, "-vn", "-ac", "1",
                            "-ar", "16000", clip], check=True)
            segs, _ = model.transcribe(clip, word_timestamps=True, language=language,
                                       vad_filter=False)
            got = []
            for s in segs:
                for w in (s.words or []):
                    if w.probability >= 0.5:
                        got.append({"word": w.word.strip(), "start": round(start + w.start, 3),
                                    "end": round(start + w.end, 3), "prob": round(w.probability, 3),
                                    "recovered": True})
        finally:
            try:
                os.remove(clip)
            except OSError:
                pass
        text = " ".join(g["word"] for g in got).strip().lower()
        if got and text not in HALLUCINATIONS:
            found.extend(got)
        elif b - a <= max_noise_s:
            noise.append({"start": round(max(0.0, a - 0.03), 3), "end": round(b + 0.03, 3),
                          "reason": "noise: a %.1fs sound with no words in it" % (b - a),
                          "kind": "noise"})
    merged = sorted(list(words) + found, key=lambda w: w.get("start", 0))
    return merged, noise


_MODEL = []


def _model():
    if not _MODEL:
        from faster_whisper import WhisperModel
        try:
            m = WhisperModel("small", device="cuda", compute_type="float16")
            list(m.transcribe(_silence_wav(), language="en")[0])
        except Exception:
            m = WhisperModel("small", device="cpu", compute_type="int8")
        _MODEL.append(m)
    return _MODEL[0]


def _silence_wav():
    import os
    import tempfile
    import wave
    fd, p = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)
    return p
