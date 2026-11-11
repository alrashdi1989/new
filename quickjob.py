"""
Build a download job from a single copied link, using the settings the app
saved. Used by the floating bubble so it can start a download on its own,
without opening the app. No Kivy here, so a background service can import it.
"""

import json
import os

import links


def _settings(data_dir):
    try:
        with open(os.path.join(data_dir, "settings.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _get(store, key, field, default=None):
    try:
        return store[key][field]
    except Exception:
        return default


def default_content():
    return ["stories", "highlights", "posts", "spotlight", "join"]


def _default_save_dir():
    try:
        from android.storage import primary_external_storage_path  # type: ignore
        return os.path.join(primary_external_storage_path(), "Download", "X-media")
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Downloads", "X-media")


def build_quick(text, data_dir):
    """Return (spec, message). spec is None when there's nothing to download;
    message is a short line to show the user either way."""
    found, _bad = links.parse_links(text or "")
    if not found:
        return None, "Copy a link first (X, TikTok, Snapchat, Instagram, Threads, "\
                     "Facebook, YouTube or a website), then tap the button."

    store = _settings(data_dir)
    save_dir = _get(store, "folder", "path") or _default_save_dir()

    token = _get(store, "auth", "token", "") or ""
    photos = _get(store, "include", "photos", True)
    videos = _get(store, "include", "videos", True)
    reposts = _get(store, "include", "reposts", False)
    rename = _get(store, "names", "rename", True)
    content = set(_get(store, "content", "picked", None) or default_content())
    only_combined = bool(_get(store, "content", "only_combined", False))
    status_dir = os.path.join(data_dir, "job")

    jobs = []
    for site, user, gurl in found:
        out = links.output_dir(save_dir, site, user)
        if site == "youtube":
            jobs.append({"args": [], "out": out, "out_label": links.pretty_path(out),
                         "site": site, "user": user, "status_dir": status_dir,
                         "yt": {"url": gurl, "audio_only": not videos}})
            continue
        if site == "web":
            gdl_out = os.path.join(out, "Images")
            jobs.append({
                "args": ["-d", gdl_out, "--download-archive",
                         os.path.join(gdl_out, ".downloaded.sqlite3"),
                         "-o", "path-restrict=windows", "-o", "path-strip=windows", gurl],
                "out": out, "out_label": links.pretty_path(out), "site": site, "user": user,
                "status_dir": status_dir,
                "yt": {"url": gurl, "audio_only": not videos,
                       "format": links.WEB_FORMAT_HINT, "name": user,
                       "folder": "%(extractor_key)s"}})
            continue
        cookies = _get(store, site, "cookies") if site in links.SITE_NAMES else None
        ua = _get(store, site, "ua")
        jobs.append({
            "args": links.build_args(site, gurl, out, token, photos, videos, reposts,
                                     cookies, ua, content, rename=rename),
            "out": out, "out_label": links.pretty_path(out), "site": site, "user": user,
            "status_dir": status_dir, "rename": rename,
            "join_stories": "join" in content and "stories" in content,
            "story_only_combined": only_combined,
        })

    for i, j in enumerate(jobs, 1):
        j["pos"] = [i, len(jobs)]
    spec = {"jobs": jobs, "status_dir": status_dir, "lane": 1, "lanes": [status_dir]}

    if len(jobs) == 1:
        site, user = found[0][0], found[0][1]
        name = links.SITE_NAMES.get(site, site)
        where = f"from {user}" if site == "web" else f"{name} @{user}"
        msg = f"Downloading {where}…"
    else:
        msg = f"Downloading {len(jobs)} links…"
    return spec, msg
