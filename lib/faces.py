"""Find where the speaker's face is, so on-screen text can stay out of its way.

Uses the Ultra-Light-Fast-Generic face detector (MIT licence, 1.5 MB, shipped in
models/). It runs on onnxruntime and numpy, which faster-whisper already
installs, so this adds nothing to anybody's setup. OpenCV would be the obvious
choice and is deliberately avoided: it is a large install students won't have.

The model takes a 640x480 landscape image. A vertical frame is letterboxed into
it, keeping its proportions, rather than stretched: stretching a 9:16 frame
into 4:3 squashes faces sideways and the detector stops recognising them.
"""
import os
import subprocess

import numpy as np

MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "face-rfb-640.onnx")
_IN_W, _IN_H = 640, 480
_session = None


def _model():
    global _session
    if _session is None:
        import onnxruntime as ort
        so = ort.SessionOptions()
        # The export exposes its weights as graph inputs, which makes onnxruntime
        # print a warning per layer -- hundreds of lines that bury real output.
        so.log_severity_level = 3
        _session = ort.InferenceSession(MODEL, sess_options=so, providers=["CPUExecutionProvider"])
    return _session


def frame_at(video, t, width=None, height=None):
    """One RGB frame as a numpy array. Seeks AFTER the input, so times are exact."""
    cmd = ["ffmpeg", "-v", "error", "-i", os.path.abspath(video), "-ss", "%.3f" % t,
           "-frames:v", "1"]
    if width and height:
        cmd += ["-vf", "scale=%d:%d" % (width, height)]
    cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    if not width:
        info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                               "-show_entries", "stream=width,height", "-of", "csv=p=0",
                               os.path.abspath(video)], capture_output=True, text=True).stdout
        width, height = [int(x) for x in info.strip().split(",")[:2]]
    if len(raw) < width * height * 3:
        return None
    return np.frombuffer(raw[:width * height * 3], dtype=np.uint8).reshape(height, width, 3)


def detect(rgb, threshold=0.7):
    """Faces in an RGB frame as (x1, y1, x2, y2, score) in that frame's pixels."""
    h, w = rgb.shape[:2]
    scale = min(_IN_W / float(w), _IN_H / float(h))
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    small = _resize(rgb, nw, nh)
    canvas = np.zeros((_IN_H, _IN_W, 3), dtype=np.uint8)
    ox, oy = (_IN_W - nw) // 2, (_IN_H - nh) // 2
    canvas[oy:oy + nh, ox:ox + nw] = small
    x = (canvas.astype(np.float32) - 127.0) / 128.0
    x = np.transpose(x, (2, 0, 1))[None, ...]
    scores, boxes = _model().run(None, {"input": x})
    scores, boxes = scores[0][:, 1], boxes[0]
    keep = scores > threshold
    found = []
    for (x1, y1, x2, y2), s in zip(boxes[keep], scores[keep]):
        # boxes are normalised to the 640x480 canvas; undo the letterbox
        bx1 = (x1 * _IN_W - ox) / scale
        by1 = (y1 * _IN_H - oy) / scale
        bx2 = (x2 * _IN_W - ox) / scale
        by2 = (y2 * _IN_H - oy) / scale
        found.append((max(0, bx1), max(0, by1), min(w, bx2), min(h, by2), float(s)))
    return _nms(found)


def _resize(rgb, w, h):
    from PIL import Image
    return np.asarray(Image.fromarray(rgb).resize((w, h), Image.BILINEAR))


def _nms(boxes, iou=0.3):
    boxes = sorted(boxes, key=lambda b: -b[4])
    out = []
    for b in boxes:
        if all(_iou(b, k) < iou for k in out):
            out.append(b)
    return out


def _iou(a, b):
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def faces_over(video, start, end, samples=6, width=None, height=None):
    """Faces seen across a stretch of video, as {time: [boxes]}.

    Sampled, not one frame: people move, and a box that was clear of the face
    at the first frame can sit on it two seconds later.
    """
    out = {}
    for i in range(samples):
        t = start + (end - start) * (i + 0.5) / samples
        f = frame_at(video, t, width, height)
        if f is not None:
            out[t] = detect(f)
    return out
