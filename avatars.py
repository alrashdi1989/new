"""
Profile photos for saved favorites.

Finds the account's profile picture with the download engine's avatar readers
(or yt-dlp for YouTube channels) and saves a copy in the app's own storage.
"""

import io
import os
import time

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
MAX_AGE = 7 * 24 * 3600        # refresh weekly

PAGES = {
    "x": "https://x.com/{name}/photo",
    "instagram": "https://www.instagram.com/{name}/avatar/",
    "tiktok": "https://www.tiktok.com/@{name}/avatar",
    "snapchat": "https://www.snapchat.com/@{name}/avatar",
    "facebook": "https://www.facebook.com/{name}/avatar",
}


def needs_refresh(path):
    try:
        return time.time() - os.path.getmtime(path) > MAX_AGE
    except OSError:
        return True


def _youtube_avatar(link):
    import yt_dlp
    channel = link.rsplit("/videos", 1)[0]
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True,
                           "playlist_items": "0", "logger": _Quiet()}) as ydl:
        info = ydl.extract_info(channel, download=False, process=False) or {}
    thumbs = info.get("thumbnails") or []
    for t in thumbs:
        if t.get("id") in ("avatar_uncropped", "avatar"):
            return t.get("url")
    square = [t for t in thumbs if t.get("width") and t.get("width") == t.get("height")]
    pick = max(square or thumbs, key=lambda t: t.get("width") or 0, default=None)
    return pick and pick.get("url")


class _Quiet:
    def debug(self, msg):
        pass

    info = warning = error = debug


def avatar_url(site, name, link, cookies=None):
    if site == "youtube":
        if "/playlist" in link or "watch?v=" in link:
            return None
        return _youtube_avatar(link)
    page = PAGES.get(site)
    if not page or not name:
        return None
    from gallery_dl import config, job
    from gallery_dl.extractor.message import Message
    config.clear()
    if cookies:
        config.set(("extractor",), "cookies", cookies)
    j = job.DataJob(page.format(name=name), file=io.StringIO())
    j.run()
    for msg in j.data:
        if msg and msg[0] == Message.Url:
            return msg[1]
    return None


def fetch(site, name, link, out_path, cookies=None):
    """Save the profile photo to out_path. Returns True on success."""
    import requests
    url = avatar_url(site, name, link, cookies)
    if not url:
        return False
    r = requests.get(url, headers={"User-Agent": UA}, cookies=cookies or {}, timeout=30)
    if r.status_code != 200 or not r.content or len(r.content) < 200:
        return False
    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        f.write(r.content)
    os.replace(tmp, out_path)
    return True
