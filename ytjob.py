"""
YouTube downloads for the Media Downloader engine (uses yt-dlp).

HD videos on YouTube come as separate video and audio streams. On the phone
there is no ffmpeg program, so yt-dlp downloads the two parts and this module
joins them with PyAV (ffmpeg's libraries): a stream copy, no re-encoding.
"""

import heapq
import os
import re
import time

VIDEO_FORMAT = ("bv*[vcodec^=avc1][height<=1080][ext=mp4]+ba[ext=m4a]"
                "/b[ext=mp4][height<=1080]/b[ext=mp4]/b")
AUDIO_FORMAT = "ba[ext=m4a]/ba"
# Other sites: prefer formats the phone can join (MP4 + M4A), else a single file.
WEB_FORMAT = ("bv*[ext=mp4][height<=1080]+ba[ext=m4a]/b[ext=mp4][height<=1080]"
              "/b[height<=1080]/b")


# ---------------------------------------------------------------- joining

def _add_stream_like(out, stream):
    try:
        return out.add_stream(template=stream)            # PyAV <= 13
    except (TypeError, ValueError, AttributeError):
        return out.add_stream_from_template(stream)       # PyAV 14+


def join_parts(parts, out_path):
    """Copy the first video/audio stream of each part into one MP4,
    interleaving packets by time so memory use stays small."""
    import av
    tmp = out_path + ".joining.mp4"
    out = av.open(tmp, "w", format="mp4")
    inputs, feeds = [], []
    try:
        for path in parts:
            ic = av.open(path)
            inputs.append(ic)
            st = next((s for s in ic.streams if s.type in ("video", "audio")), None)
            if st is None:
                continue
            feeds.append((ic.demux(st), _add_stream_like(out, st)))

        heap = []

        def push(i):
            gen, ost = feeds[i]
            for pkt in gen:
                if pkt.dts is None:          # flush packet at the end of a stream
                    continue
                t = float((pkt.pts if pkt.pts is not None else pkt.dts) * pkt.time_base)
                heapq.heappush(heap, (t, i, id(pkt), pkt))
                return

        for i in range(len(feeds)):
            push(i)
        while heap:
            _, i, _, pkt = heapq.heappop(heap)
            pkt.stream = feeds[i][1]
            out.mux(pkt)
            push(i)
    finally:
        out.close()
        for ic in inputs:
            ic.close()
    os.replace(tmp, out_path)


def make_join_pp(yt_dlp):
    from yt_dlp.postprocessor.common import PostProcessor

    class PyAVJoinPP(PostProcessor):
        """Join separately downloaded video+audio when ffmpeg isn't installed."""

        def run(self, info):
            fmts = info.get("requested_formats") or []
            parts = [f.get("filepath") for f in fmts if f.get("filepath")]
            if len(parts) < 2 or not all(os.path.exists(p) for p in parts):
                return [], info              # already joined (ffmpeg) or a single file
            base = re.sub(r"\.f[\w-]+\.\w+$", "", parts[0])
            final = f"{base}.mp4"
            self.to_screen(f"Joining video and audio into {os.path.basename(final)}")
            try:
                join_parts(parts, final)
            except Exception as e:                # keep the parts rather than lose the download
                self.report_warning(f"Couldn't join video and audio ({e}); "
                                    "kept them as separate files")
                return [], info
            for p in parts:
                try:
                    os.remove(p)
                except OSError:
                    pass
            info["filepath"] = final
            info["ext"] = "mp4"
            return [], info

    return PyAVJoinPP


# ---------------------------------------------------------------- running

def _size(n):
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def run(job):
    """Download job.spec['yt'] = {'url': ..., 'audio_only': bool}.
    Returns (rc, result) like the gallery-dl path."""
    import yt_dlp
    from yt_dlp.utils import DownloadCancelled

    spec, yt = job.spec, job.spec["yt"]
    out = spec["out"]
    os.makedirs(out, exist_ok=True)
    state = {"last": 0.0}

    tag = "youtube" if spec.get("site") == "youtube" else "web"

    class Logger:
        def debug(self, msg):
            if msg.startswith("[debug]"):
                return
            if msg.startswith(("[download]", "[youtube] Extracting", "[info]")) and "%" in msg:
                return                        # progress lines are shown in the status instead
            job.log(msg)

        def info(self, msg):
            self.debug(msg)

        def warning(self, msg):
            job.log(f"[{tag}][warning] {msg}")
            job.last_error = msg

        def error(self, msg):
            job.log(f"[{tag}][error] {msg}")
            job.last_error = msg

    def on_progress(d):
        if job.cancelled():
            raise DownloadCancelled()
        info = d.get("info_dict") or {}
        idx, n = info.get("playlist_index"), info.get("n_entries") or info.get("playlist_count")
        if n:
            job.total = int(n)
            if idx:
                job.posts.add(str(idx))
        if d.get("status") != "downloading" or time.time() - state["last"] < 1:
            return
        state["last"] = time.time()
        done, total = d.get("downloaded_bytes") or 0, d.get("total_bytes") or d.get("total_bytes_estimate")
        pct = f"{done * 100 / total:.0f}% of {_size(total)}" if total else _size(done)
        which = f"video {idx} of {n} · " if (idx and n) else ""
        part = ""
        if info.get("vcodec") == "none" and not yt.get("audio_only"):
            part = " (audio)"
        job.save(count=job.count, total=job.total, seen=len(job.posts),
                 status=f"{job.prefix}{which}{pct}{part}")

    def on_done(path):
        job.count += 1
        job.new_files.append(path)
        job.log("+ " + os.path.basename(path))
        job.record_file(path)
        job.save(count=job.count, total=job.total, seen=len(job.posts),
                 status=job.progress_text())
        job.notify(f"{job.prefix}{yt.get('name', 'YouTube')}: {job.count} downloaded")

    opts = {
        "outtmpl": {"default": os.path.join(
            out, yt.get("folder", "%(uploader,channel|YouTube)s"), "%(title).120B [%(id)s].%(ext)s")},
        "windowsfilenames": True,                 # Android shared storage rejects : ? * etc.
        "format": AUDIO_FORMAT if yt.get("audio_only") else yt.get("format", VIDEO_FORMAT),
        "merge_output_format": "mp4",
        "ignoreerrors": True,                     # keep going; also lets parts download without ffmpeg
        "download_archive": os.path.join(out, ".downloaded-youtube.txt"),
        "logger": Logger(),
        "quiet": True,
        "noprogress": True,
        "progress_hooks": [on_progress],
        "post_hooks": [on_done],
        "concurrent_fragment_downloads": 3,
        "retries": 5,
        "noplaylist": False,
    }
    if yt.get("ffmpeg_location"):
        opts["ffmpeg_location"] = yt["ffmpeg_location"]

    rc, result = 1, "error"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.add_post_processor(make_join_pp(yt_dlp)(ydl), when="post_process")
            if yt.get("info"):                    # used by tests
                ydl.process_ie_result(yt["info"], download=True)
                rc = 0 if not job.last_error.startswith(("ERROR", "Unable")) else 1
            else:
                rc = ydl.download([yt["url"]])
        result = "done" if rc == 0 else "error"
    except DownloadCancelled:
        result = "cancelled"
    except Exception as e:
        job.log(f"Error: {e}")
        job.last_error = str(e)
    if job.cancelled():
        result = "cancelled"
    return rc, result
