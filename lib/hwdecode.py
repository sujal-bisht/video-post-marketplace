"""Decode the camera footage on the graphics chip when that is genuinely faster.

WHY
---
A 4K phone video is expensive to decode: on a 20-thread laptop, software
decoding of 10-bit HEVC from a DJI ran at 0.7x real time, so the final render
spent most of its time just reading the footage, and the encoder, the zoom and
the captions fought it for the same cores. Decoding on the GPU and scaling to
the post size there before the frames come back hands those cores back:
the same 30 seconds of render took 27s instead of 45s.

It is not always a win, so nothing is assumed. "Hardware decoding" through the
generic Windows route (d3d11va) was SLOWER than software on the same machine,
because every 4K frame has to be copied back from the GPU. What made the
difference was Intel Quick Sync with the scaling done on the GPU, so only the
small frame is copied back. On a Mac the equivalent is VideoToolbox.

So each candidate is timed against software on a few seconds of the user's own
video, and used only if it is clearly faster and produces the same number of
frames. The answer is remembered per kind of footage in ~/.video-post/hw.json,
so the test runs once per camera, not once per video. If anything about the
hardware path fails, it falls back to software: this can make a render faster,
never make one fail.

Checked on the DJI footage: identical frame count, the same frame at the same
time, a mean difference of 1-2 levels of 255 after scaling -- invisible.
"""
import json
import os
import subprocess
import sys
import time

CACHE = os.path.join(os.path.expanduser("~"), ".video-post", "hw.json")
SAMPLE = 6.0          # seconds timed per candidate; shorter and start-up time swamps it
MUST_BEAT = 0.8       # a hardware path must take at most 80% of software's time
MATCH_W, MATCH_H = 96, 96   # tiny frames, compared to prove the GPU decodes the same pictures
MAX_DIFF = 6.0        # mean difference (of 255) above which the frames are not the same


def _candidates(out_w, out_h):
    """(name, input args, filter that leaves software frames at out_w x out_h)."""
    sw_scale = "scale=%d:%d:flags=lanczos" % (out_w, out_h)
    if sys.platform == "darwin":
        return [("videotoolbox", ["-hwaccel", "videotoolbox"], sw_scale)]
    if os.name == "nt":
        # extra_hw_frames: the cut splits the decoded stream into one branch per
        # kept piece, and each branch can hold a frame for a moment; without
        # spare surfaces the decoder's small pool can run dry.
        return [("qsv", ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv",
                         "-extra_hw_frames", "16"],
                 "vpp_qsv=w=%d:h=%d:format=nv12,hwdownload,format=nv12" % (out_w, out_h))]
    return []


def _probe(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,pix_fmt,width,height,r_frame_rate:format=duration",
                          "-of", "json", video], capture_output=True, text=True).stdout
    d = json.loads(out)
    s = d["streams"][0]
    return s, float(d["format"].get("duration", 0) or 0)


def _time(args, video, vf, start):
    """Seconds to decode + encode SAMPLE seconds, as a real render runs.

    Timed on decoding alone, the GPU looked barely faster (22s vs 24s) and lost
    the test -- but its whole value is handing the processor back to the
    encoder, which only shows when both run: 27s vs 45s for the same 30s.
    """
    cmd = ["ffmpeg", "-v", "error"] + args + ["-ss", "%.2f" % start, "-t", "%.2f" % SAMPLE,
                                              "-i", video, "-an", "-vf", vf,
                                              "-c:v", "libx264", "-preset", "veryfast",
                                              "-crf", "20", "-f", "null", "-"]
    t = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    return time.time() - t if r.returncode == 0 else None


def _frames(args, video, vf):
    """The first four seconds as tiny grey frames, decoded from the very start.

    No seeking: a seek lands slightly differently on each path, and the point
    here is to compare the decoders, not the seeks.
    """
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error"] + args + ["-t", "4", "-i", video, "-an",
                        "-vf", vf + ",format=gray", "-f", "rawvideo", "-"], capture_output=True)
    if r.returncode != 0:
        return None
    n = MATCH_W * MATCH_H
    data = np.frombuffer(r.stdout, np.uint8)
    return data[:len(data) // n * n].reshape(-1, MATCH_H, MATCH_W).astype(float)


def _offset(hw, sw):
    """How many frames the GPU's pictures sit away from their timestamps, or None.

    Measured, not assumed. Intel Quick Sync on the DJI footage labelled every
    picture one frame late, so the final showed each moment 1/60s early -- found
    by checking the render frame by frame against the original. Whatever the
    offset is on a given machine, it is corrected; if the pictures do not match
    at all, the GPU path is not used.

    Compared in ways that ignore brightness: converting 10-bit colour to grey
    differs by ~10 levels between the two paths, which drowned a plain
    difference. The offset comes from each path's MOTION -- how much every frame
    changes from the one before -- lined up against the other; whether the
    pictures are the same comes from their correlation once lined up.
    """
    import numpy as np
    if hw is None or sw is None or len(hw) < 20 or len(sw) < 20:
        return None

    def motion(frames):
        return np.array([abs(frames[i] - frames[i - 1]).mean() for i in range(1, len(frames))])

    def corr(a, b):
        a, b = a - a.mean(), b - b.mean()
        den = float(np.sqrt((a * a).sum() * (b * b).sum()))
        return float((a * b).sum() / den) if den else 0.0

    mh, ms = motion(hw), motion(sw)
    scores = {}
    for k in range(-3, 4):
        idx = [i for i in range(3, len(mh) - 3) if 0 <= i + k < len(ms)]
        scores[k] = corr(mh[idx], ms[[i + k for i in idx]])
    k = max(scores, key=scores.get)
    others = [v for j, v in scores.items() if j != k]
    if scores[k] < 0.9 or (others and scores[k] - max(others) < 0.02):
        return None          # no clear alignment: too little motion, or different pictures
    idx = [i for i in range(3, len(hw) - 3) if 0 <= i + k < len(sw)]
    same = np.mean([corr(hw[i], sw[i + k]) for i in idx])
    return k if same >= 0.95 else None


def _with_shift(vf, shift):
    # hw picture i shows software frame i + shift, which belongs shift frames later.
    return vf if not shift else "%s,setpts=PTS%+d/(FRAME_RATE*TB)" % (vf, shift)


def choose(video, out_w, out_h):
    """(input args, head filter) for decoding `video` to out_w x out_h.

    The head filter turns decoded frames into ordinary software frames at the
    post size. It goes AFTER the cut in the render graph: the cut needs the
    camera's own timestamps, and the GPU scaler replaces them.
    """
    sw = ([], "scale=%d:%d:flags=lanczos" % (out_w, out_h))
    if os.environ.get("VIDEO_POST_NO_HW"):
        return sw
    try:
        stream, dur = _probe(video)
    except Exception:
        return sw
    key = "%s/%s/%sx%s/%s->%dx%d" % (stream.get("codec_name"), stream.get("pix_fmt"),
                                     stream.get("width"), stream.get("height"),
                                     stream.get("r_frame_rate"), out_w, out_h)
    cache = {}
    try:
        with open(CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        pass
    hit = cache.get(key)
    if isinstance(hit, dict):
        for name, args, vf in _candidates(out_w, out_h):
            if name == hit.get("decoder"):
                return args, _with_shift(vf, hit.get("shift", 0))
        return sw
    if hit is not None and not isinstance(hit, dict) and hit == "software":
        return sw

    start = min(5.0, max(0.0, dur / 3.0))
    sw_time = _time([], video, sw[1], start)
    choice = {"decoder": "software"}
    shrink = "scale=%d:%d:flags=area" % (MATCH_W, MATCH_H)
    small_sw = _frames([], video, shrink)
    if sw_time:
        for name, args, vf in _candidates(out_w, out_h):
            took = _time(args, video, vf, start)
            if not took or took > sw_time * MUST_BEAT:
                continue
            # The real path at the real size, then shrunk in software exactly
            # like the software frames. Shrinking on the GPU straight to a
            # thumbnail was tried and is too noisy to compare.
            shift = _offset(_frames(args, video, vf + "," + shrink), small_sw)
            if shift is None:
                print("GPU decoding (%s) skipped: its frames did not match software's." % name)
                continue
            choice = {"decoder": name, "shift": shift}
            print("Decoding on the GPU (%s): %.1fs vs %.1fs in software for the same %.0fs%s."
                  % (name, took, sw_time, SAMPLE,
                     "" if not shift else ", timestamps corrected by %+d frame" % shift))
            break
    cache[key] = choice
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass
    for name, args, vf in _candidates(out_w, out_h):
        if name == choice["decoder"]:
            return args, _with_shift(vf, choice.get("shift", 0))
    return sw
