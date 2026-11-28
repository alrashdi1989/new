"""
Pull the audio out of a video file and save it on its own.

There is no ffmpeg program on the phone, only PyAV (ffmpeg's libraries). When the
video's audio is already AAC (the usual case for MP4) we copy it straight into an
.m4a with no re-encoding - instant and lossless. Otherwise we re-encode to AAC,
which PyAV can always do. MP3 needs an encoder the phone build doesn't include,
so the output is .m4a (plays on every phone, player and computer).
"""

import os


def _audio_stream(container):
    for s in container.streams:
        if s.type == "audio":
            return s
    return None


def extract_audio(src, dst):
    """Write the audio of `src` to `dst` (.m4a). Returns dst. Raises on failure."""
    import av

    tmp = dst + ".part.m4a"
    inp = av.open(src)
    out = None
    try:
        astream = _audio_stream(inp)
        if astream is None:
            raise ValueError("This file has no audio.")
        out = av.open(tmp, "w", format="mp4")           # .m4a is an MP4 audio container
        codec = (astream.codec_context.name or "").lower()

        if codec == "aac":
            # copy the audio stream as-is (no quality loss, very fast)
            ostream = _add_like(out, astream)
            for pkt in inp.demux(astream):
                if pkt.dts is None:
                    continue
                pkt.stream = ostream
                out.mux(pkt)
        else:
            _transcode_aac(inp, astream, out)
    finally:
        if out is not None:
            out.close()
        inp.close()
    os.replace(tmp, dst)
    return dst


def _add_like(out, stream):
    try:
        return out.add_stream(template=stream)          # PyAV <= 13
    except (TypeError, ValueError, AttributeError):
        return out.add_stream_from_template(stream)      # PyAV 14+


def _transcode_aac(inp, astream, out):
    import av
    from av.audio.resampler import AudioResampler

    rate = astream.codec_context.sample_rate or 44100
    ostream = out.add_stream("aac", rate=rate)
    try:
        layout = astream.codec_context.layout.name
    except Exception:
        layout = "stereo"
    if layout not in ("mono", "stereo"):
        layout = "stereo"
    resampler = AudioResampler(format=ostream.format, layout=layout, rate=rate)

    def mux(frames):
        for fr in frames:
            for pkt in ostream.encode(fr):
                out.mux(pkt)

    for frame in inp.decode(astream):
        frame.pts = None
        r = resampler.resample(frame)
        mux(r if isinstance(r, list) else [r] if r else [])
    r = resampler.resample(None)                         # flush resampler
    mux(r if isinstance(r, list) else [r] if r else [])
    for pkt in ostream.encode():                         # flush encoder
        out.mux(pkt)


def out_name(src_name):
    """A friendly .m4a name from the source file name."""
    base = os.path.splitext(os.path.basename(src_name))[0].strip() or "audio"
    return base + ".m4a"
