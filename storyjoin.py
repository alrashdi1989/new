"""
Join a story (photos + videos, in order) into one MP4.

Every item is fitted into a 720x1280 frame (black bars where needed) at 30 fps.
Photos show for PHOTO_SECONDS with silence; videos keep their sound.
Encoding: H.264 (libx264) + AAC, so the result plays everywhere.
"""

import os
from fractions import Fraction

W, H, FPS = 720, 1280, 30
RATE = 44100
PHOTO_SECONDS = 5
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".3gp")


def _pull_all(sink):
    out = []
    while True:
        try:
            out.append(sink.pull())
        except Exception:          # EAGAIN / EOF: nothing more for now
            return out


def _fit_graph(av, frame):
    """scale to fit 720x1280, pad with black, 30 fps, yuv420p."""
    g = av.filter.Graph()
    src = g.add_buffer(width=frame.width, height=frame.height, format=frame.format.name,
                       time_base=frame.time_base or Fraction(1, FPS))
    chain = [
        g.add("scale", f"{W}:{H}:force_original_aspect_ratio=decrease"),
        g.add("pad", f"{W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black"),
        g.add("setsar", "1"),
        g.add("format", "yuv420p"),
    ]
    sink = g.add("buffersink")
    prev = src
    for f in chain + [sink]:
        prev.link_to(f)
        prev = f
    g.configure()
    return src, sink


class _Writer:
    def __init__(self, av, path):
        self.av = av
        self.out = av.open(path, "w", format="mp4")
        self.v = self.out.add_stream("libx264", rate=FPS)
        self.v.width, self.v.height, self.v.pix_fmt = W, H, "yuv420p"
        try:
            self.v.codec_context.options = {"preset": "ultrafast", "crf": "26",
                                            "tune": "fastdecode"}
        except Exception:
            pass
        self.a = self.out.add_stream("aac", rate=RATE)
        try:
            self.a.layout = "stereo"
        except Exception:
            self.a.codec_context.layout = "stereo"
        self.fifo = av.AudioFifo()
        self.resampler = av.AudioResampler(format="fltp", layout="stereo", rate=RATE)
        self.vframes = 0        # video frames written
        self.asamples = 0       # audio samples written

    # ---- video
    def video(self, frame):
        frame.pts = self.vframes
        frame.time_base = Fraction(1, FPS)
        for p in self.v.encode(frame):
            self.out.mux(p)
        self.vframes += 1

    # ---- audio
    def _encode_fifo(self, final=False):
        while self.fifo.samples >= 1024 or (final and self.fifo.samples):
            f = self.fifo.read(min(1024, self.fifo.samples))
            f.pts = self.asamples
            f.time_base = Fraction(1, RATE)
            self.asamples += f.samples
            for p in self.a.encode(f):
                self.out.mux(p)

    def audio(self, frame):
        frame.pts = None
        for r in self.resampler.resample(frame) or []:
            r.pts = None
            self.fifo.write(r)
        self._encode_fifo()

    def silence(self, samples):
        while samples > 0:
            n = min(1024, samples)
            f = self.av.AudioFrame(format="fltp", layout="stereo", samples=n)
            for plane in f.planes:
                plane.update(bytes(plane.buffer_size))
            f.sample_rate = RATE
            f.pts = None
            self.fifo.write(f)
            samples -= n
        self._encode_fifo()

    def sync_audio(self):
        """Pad with silence or drop audio so sound ends where the picture ends."""
        target = round(self.vframes * RATE / FPS)
        have = self.asamples + self.fifo.samples
        if have < target:
            self.silence(target - have)
        elif have > target and self.fifo.samples:
            drop = min(have - target, self.fifo.samples)
            keep = self.fifo.samples - drop
            if keep:
                f = self.fifo.read(keep)
                self.fifo = self.av.AudioFifo()
                f.pts = None
                self.fifo.write(f)
            else:
                self.fifo = self.av.AudioFifo()
            self._encode_fifo()

    def close(self):
        self._encode_fifo(final=True)
        for p in self.a.encode(None):
            self.out.mux(p)
        for p in self.v.encode(None):
            self.out.mux(p)
        self.out.close()


def _add_photo(av, w, path):
    with av.open(path) as c:
        frame = next(c.decode(video=0))
    src, sink = _fit_graph(av, frame)
    frame.pts = 0
    src.push(frame)
    src.push(None)
    fitted = _pull_all(sink)
    if not fitted:
        return False
    still = fitted[0]
    for _ in range(PHOTO_SECONDS * FPS):
        f = still.reformat()            # fresh copy each time (encoder may keep a reference)
        w.video(f)
    w.silence(round(PHOTO_SECONDS * RATE))
    w.sync_audio()
    return True


def _add_video(av, w, path):
    with av.open(path) as c:
        vs = next((s for s in c.streams if s.type == "video"), None)
        if vs is None:
            return False
        aus = next((s for s in c.streams if s.type == "audio"), None)
        streams = [vs] + ([aus] if aus is not None else [])
        # each clip can have a different sound format: fresh converter per clip
        w.resampler = av.AudioResampler(format="fltp", layout="stereo", rate=RATE)
        start = w.vframes
        graph = None
        last_t = None
        step = 1.0 / FPS
        for packet in c.demux(*streams):
            for frame in packet.decode():
                if packet.stream.type == "audio":
                    w.audio(frame)
                    continue
                if graph is None:
                    graph = _fit_graph(av, frame)
                src, sink = graph
                t = float(frame.pts * frame.time_base) if frame.pts is not None else None
                src.push(frame)
                for f in _pull_all(sink):
                    # constant 30 fps: repeat or skip frames to follow the source timing
                    if t is None:
                        w.video(f)
                        continue
                    if last_t is None:
                        last_t = t - step
                    while last_t + step <= t + 1e-6:
                        w.video(f.reformat())
                        last_t += step
        if graph is not None:
            try:
                graph[0].push(None)
                for f in _pull_all(graph[1]):
                    w.video(f)
            except Exception:
                pass
        if aus is None:
            w.silence(round((w.vframes - start) * RATE / FPS))
        w.sync_audio()
        return w.vframes > start


def join_story(files, out_path, log=print):
    """files: story items in order. Returns number of items joined."""
    import av
    items = [p for p in files if p.lower().endswith(IMAGE_EXTS + VIDEO_EXTS) and os.path.exists(p)]
    if len(items) < 2:
        return 0
    tmp = out_path + ".joining.mp4"
    w = _Writer(av, tmp)
    joined = 0
    try:
        for p in items:
            try:
                ok = (_add_photo(av, w, p) if p.lower().endswith(IMAGE_EXTS)
                      else _add_video(av, w, p))
                joined += 1 if ok else 0
            except Exception as e:
                log(f"Skipped {os.path.basename(p)} in the story video ({e})")
    finally:
        w.close()
    if joined < 1:
        os.remove(tmp)
        return 0
    os.replace(tmp, out_path)
    return joined
