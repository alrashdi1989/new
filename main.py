"""
X Media Downloader — Android (Kivy) version.

Paste an X profile URL, paste your x.com `auth_token` cookie once,
tap Download. Files go to  Download/X-media/<username>/  on the phone.
"""

import io
import json
import os
import re
import sys
import threading
import time

from kivy.app import App
from kivy.utils import escape_markup
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.storage.jsonstore import JsonStore
from kivy.lang import Builder
from kivy.properties import (BooleanProperty, ListProperty, NumericProperty, ObjectProperty,
                             StringProperty)
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.uix.gridlayout import GridLayout  # noqa: F401 (used in KV)
from kivy.uix.modalview import ModalView
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton
from kivy.utils import platform

import engine

RESERVED = {"home", "explore", "search", "notifications", "messages", "i", "settings",
            "compose", "login", "logout", "signup", "tos", "privacy", "hashtag", "intent"}
FB_RESERVED = {"groups", "events", "marketplace", "watch", "reel", "reels", "share", "story.php",
               "login", "login.php", "pages", "gaming", "help", "settings", "photo", "photo.php",
               "media", "profile.php", "people", "hashtag", "search", "friends", "messages"}
APP_VERSION = "6.1"
IG_RESERVED = {"explore", "accounts", "direct", "reels", "stories", "p", "reel", "tv",
               "about", "legal", "developer", "web", "challenge", "emails", "session"}
IMAGE_EXT = ("jpg", "jpeg", "png", "webp")
VIDEO_EXT = ("mp4", "mov", "m4v", "webm")


def parse_username(text):
    """X username from a profile link, @name or bare name (kept for compatibility)."""
    text = text.strip()
    m = re.match(r"^(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/([^/?#]+)", text, re.I)
    name = m.group(1) if m else text.lstrip("@")
    if re.fullmatch(r"[A-Za-z0-9_]{1,15}", name) and name.lower() not in RESERVED:
        return name
    return None


def resolve_link(text):
    """Return (site, name, url) for an X or TikTok link, or None.

    site is "x", "tiktok", "snapchat" or "instagram"; name is used for the folder and status text.
    """
    t = text.strip().split()[0] if text.strip() else ""
    if not t:
        return None
    if not re.match(r"^https?://", t, re.I) and ("." in t.split("/")[0]):
        t = "https://" + t

    # TikTok short share links (vm./vt.tiktok.com/xxxx, tiktok.com/t/xxxx)
    m = re.match(r"^https?://(?:vm|vt)\.tiktok\.com/(\w+)", t, re.I) or \
        re.match(r"^https?://(?:www\.|m\.)?tiktok\.com/t/(\w+)", t, re.I)
    if m:
        return "tiktok", "shared", t.split("?")[0]
    # TikTok single post
    m = re.match(r"^https?://(?:www\.|m\.)?tiktok\.com/@([\w.-]+)/(?:video|photo)/(\d+)", t, re.I)
    if m:
        return "tiktok", m.group(1), f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}"
    # TikTok profile
    m = re.match(r"^https?://(?:www\.|m\.)?tiktok\.com/@([\w.-]+)/?(?:[?#].*)?$", t, re.I)
    if m:
        return "tiktok", m.group(1), f"https://www.tiktok.com/@{m.group(1)}/posts"

    # Instagram
    ig = r"^https?://(?:www\.|m\.)?instagram\.com/"
    m = re.match(ig + r"(?:[\w.]+/)?(p|reel|reels|tv)/([\w-]+)", t, re.I)
    if m:
        kind = "p" if m.group(1).lower() == "p" else "reel"
        return "instagram", "posts", f"https://www.instagram.com/{kind}/{m.group(2)}/"
    m = re.match(ig + r"stories/highlights/(\d+)", t, re.I)
    if m:
        return "instagram", "highlights", f"https://www.instagram.com/stories/highlights/{m.group(1)}/"
    m = re.match(ig + r"stories/([\w.]+)(?:/(\d+))?", t, re.I)
    if m:
        u = f"https://www.instagram.com/stories/{m.group(1)}/" + (f"{m.group(2)}/" if m.group(2) else "")
        return "instagram", m.group(1), u
    m = re.match(ig + r"([\w.]{1,30})/?(?:[?#].*)?$", t, re.I)
    if m and m.group(1).lower() not in IG_RESERVED:
        return "instagram", m.group(1), f"https://www.instagram.com/{m.group(1)}/"

    # YouTube (videos, shorts, playlists, channels)
    yt = r"^https?://(?:www\.|m\.|music\.)?youtube\.com/"
    m = re.match(r"^https?://youtu\.be/([\w-]{11})", t, re.I) or \
        re.match(yt + r"(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)([\w-]{11})", t, re.I)
    if m and "list=" not in t:
        return "youtube", "videos", f"https://www.youtube.com/watch?v={m.group(1)}"
    m = re.search(r"[?&]list=([\w-]+)", t)
    if m and re.match(yt, t, re.I):
        return "youtube", "playlist", f"https://www.youtube.com/playlist?list={m.group(1)}"
    m = re.match(yt + r"(@[\w.-]+|channel/[\w-]+|c/[\w.-]+|user/[\w.-]+)", t, re.I)
    if m:
        return "youtube", m.group(1).split("/")[-1].lstrip("@"), \
            f"https://www.youtube.com/{m.group(1)}/videos"

    # Facebook
    fb = r"^https?://(?:www\.|m\.|web\.|mbasic\.)?facebook\.com/"
    if re.match(r"^https?://fb\.watch/", t, re.I) or re.match(fb + r"share/", t, re.I):
        return "facebook", "shared", t.split("#")[0]          # resolved when downloading
    m = re.match(fb + r"(?:[\w.]+/)?(?:reel|reels)/(\d+)", t, re.I)
    if m:
        return "facebook", "videos", f"https://www.facebook.com/watch/?v={m.group(1)}"
    m = re.match(fb + r"watch/?\?(?:.*&)?v=(\d+)", t, re.I) or re.match(fb + r"[\w.]+/videos/(?:[^/?#]+/)?(\d+)", t, re.I)
    if m:
        return "facebook", "videos", f"https://www.facebook.com/watch/?v={m.group(1)}"
    if re.match(fb + r"(?:photo/?\?|photo\.php\?)", t, re.I):
        # A photo link that names its album downloads the whole album
        m = re.search(r"[?&]set=([\w.-]+)", t)
        if m:
            return "facebook", "albums", f"https://www.facebook.com/media/set/?set={m.group(1)}"
        return "facebook", "photos", re.sub(r"facebook\.com/photo\.php\?", "facebook.com/photo/?",
                                            re.sub(r"^https?://(?:m|web|mbasic)\.", "https://www.", t.split("#")[0]))
    if re.match(fb + r"media/set/?\?", t, re.I):
        return "facebook", "albums", t.split("#")[0]
    m = re.match(fb + r"profile\.php\?(?:.*&)?id=(\d+)", t, re.I) or \
        re.match(fb + r"people/[^/?#]+/(\d+)", t, re.I)
    if m:
        return "facebook", m.group(1), f"https://www.facebook.com/{m.group(1)}/photos"
    m = re.match(fb + r"([\w.-]{2,80})/?(?:(?:photos|photos_by)/?)?(?:[?#].*)?$", t, re.I)
    if m and m.group(1).lower() not in FB_RESERVED:
        return "facebook", m.group(1), f"https://www.facebook.com/{m.group(1)}/photos"

    # Snapchat single highlight / story link -> download just that one
    m = re.match(r"^https?://(?:www\.)?snapchat\.com/(?:@|add/)([\w.-]+)/(?:highlight/)?[^/?#]{20,}", t, re.I)
    if m:
        return "snapchat", m.group(1), t.split("?")[0]
    # Snapchat public profile -> current story + all highlights
    m = re.match(r"^https?://(?:www\.)?snapchat\.com/(?:@|add/)([\w.-]+)/?(?:[?#].*)?$", t, re.I) or \
        re.match(r"^https?://story\.snapchat\.com/(?:s/|@)?([\w.-]+)/?(?:[?#].*)?$", t, re.I)
    if m:
        return "snapchat", m.group(1), "snapfull:" + m.group(1)

    # X single post
    m = re.match(r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/(\w{1,15})/status/(\d+)", t, re.I)
    if m:
        return "x", m.group(1), f"https://x.com/{m.group(1)}/status/{m.group(2)}"
    # X profile, @name or bare name
    user = parse_username(text)
    if user:
        return "x", user, f"https://x.com/{user}/media"
    # Any other website: video via yt-dlp, else images via the main engine
    m = re.match(r"^https?://(?:www\.|m\.)?([^/\s:?#]+\.[^/\s:?#]+)", t, re.I)
    if m:
        return "web", m.group(1).lower(), t
    return None


def parse_links(text):
    """Return ([(site, name, url), ...], bad_tokens) from text with one or more links."""
    tokens = [t.strip("<>()[]\"'.,;") for t in re.split(r"[\s,]+", text or "")]
    tokens = [t for t in tokens if t]
    many = len(tokens) > 1
    found, seen, bad = [], set(), []
    for t in tokens:
        # In a list or share text, only accept things that look like links,
        # so ordinary words aren't mistaken for X usernames.
        if many and not ("." in t or "/" in t or t.startswith("@")):
            continue
        link = resolve_link(t)
        if not link:
            if "." in t or "/" in t:
                bad.append(t)
            continue
        if link[2] not in seen:
            seen.add(link[2])
            found.append(link)
    return found, bad


def build_args(site, url, out_dir, auth_token, images, videos, retweets, site_cookies=None,
               site_ua=None, content=None, rename=False):
    content = set(content) if content is not None else {"stories", "highlights", "posts", "spotlight"}
    exts = (IMAGE_EXT if images else ()) + (VIDEO_EXT if videos else ())
    if site == "facebook" and videos:
        exts += ("m4a",)           # Facebook sends a video's sound as a separate file
    args = [
        "-d", out_dir,
        "--download-archive", os.path.join(out_dir, ".downloaded.sqlite3"),
        # Instagram: "merged" = the version with sound built in (the other kind needs
        # a desktop video tool to join picture and sound, which phones don't have)
        "-o", "videos=merged" if site == "instagram" else "videos=true",
        # Android's shared storage rejects : ? * " < > | \ in file names
        "-o", "path-restrict=windows",
    ]
    if rename:
        # temporary name carrying the post's date; the engine renames it to
        # "<profile> <date time>.<ext>" right after it's saved
        args += ["-o", "filename=@@{date}@@{id}_{num}.{extension}"]
    args += [
        "-o", "path-strip=windows",
        "-o", ('directory=["{section}"]' if site == "snapchat" else
               'directory=["{subcategory!c}"]' if (site == "tiktok" and url.endswith("/posts")
                                                   and "stories" in content) else
               'directory=["{subcategory!c}"]' if site == "instagram" and is_ig_profile(url) else
               "directory=[]"),
        "--filter", f"extension in {exts!r}",
    ]
    if site == "x":
        args += ["-o", f"retweets={'true' if retweets else 'false'}"]
        if auth_token:  # the X login is only ever sent to X
            args += ["-o", 'cookies={"auth_token": "%s"}' % auth_token.strip()]
    elif site == "tiktok":
        args += ["-o", "audio=false", "-o", "covers=false"]
        if url.endswith("/posts"):
            # List profile videos with yt-dlp first (kept up to date with TikTok's
            # site changes); the engine falls back to its own method if that fails.
            args += ["-o", "ytdl=true"]
            if "stories" in content or "posts" not in content:
                inc = [k for k in ("posts", "stories") if k in content]
                args += ["-o", "include=" + (",".join(inc) or "none")]
                url = url[:-len("posts")]          # whole profile, filtered by include
        if site_cookies:  # the TikTok login is only ever sent to TikTok
            args += ["-o", "cookies=" + json.dumps(site_cookies)]
    elif site == "snapchat":
        if url.startswith("snapfull:"):
            sec = [n for k, n in (("stories", "Story"), ("highlights", "Highlights"),
                                  ("spotlight", "Spotlight")) if k in content]
            args += ["-o", "sections=" + (",".join(sec) or "none")]
    elif site == "facebook":
        if site_cookies:  # the Facebook login is only ever sent to Facebook
            args += ["-o", "cookies=" + json.dumps(site_cookies)]
    elif site == "instagram":
        if is_ig_profile(url):
            inc = [k for k in ("stories", "highlights", "posts") if k in content]
            args += ["-o", "include=" + (",".join(inc) or "none")]
        if site_cookies:  # the Instagram login is only ever sent to Instagram
            args += ["-o", "cookies=" + json.dumps(site_cookies)]
    # Report each post's id plus the profile's post count, so the app can show
    # "post N of ~total" (only meaningful for whole profiles).
    if site == "x" and url.endswith("/media"):
        args += ["--Print", "@@META {user[media_count]}|{tweet_id}"]
    elif site == "tiktok" and url.endswith("/posts"):
        args += ["--Print", "@@META {authorStats[videoCount]}|{id}"]
    elif site == "instagram" and is_ig_profile(url):
        args += ["--Print", "@@META 0|{post_shortcode|post_id|media_id}"]
    elif site == "snapchat" and url.startswith("snapfull:"):
        args += ["--Print", "@@META {_total}|{id}_{num}_{file_id}"]
    args.append(url)
    return args


SITE_NAMES = {"x": "X", "tiktok": "TikTok", "snapchat": "Snapchat", "instagram": "Instagram",
              "facebook": "Facebook", "youtube": "YouTube", "web": "website"}
FOLDERS = {"tiktok": "TikTok", "snapchat": "Snapchat", "instagram": "Instagram",
           "facebook": "Facebook", "youtube": "YouTube", "web": "Web"}
WEB_FORMAT_HINT = ("bv*[ext=mp4][height<=1080]+ba[ext=m4a]/b[ext=mp4][height<=1080]"
                   "/b[height<=1080]/b")


def is_ig_profile(url):
    return re.fullmatch(r"https://www\.instagram\.com/[\w.]+/", url) is not None


def output_dir(save_dir, site, name):
    # X keeps its original layout so earlier downloads aren't repeated.
    if site == "x":
        return os.path.join(save_dir, name)
    if site in ("youtube", "web"):   # sorted into channel/site folders by the downloader
        return os.path.join(save_dir, FOLDERS[site])
    return os.path.join(save_dir, FOLDERS[site], name)


def download_root():
    if platform == "android":
        from android.storage import primary_external_storage_path  # type: ignore
        return os.path.join(primary_external_storage_path(), "Download", "X-media")
    return os.path.join(os.path.expanduser("~"), "Downloads", "X-media")


def pretty_path(path):
    for prefix, name in (("/storage/emulated/0/", ""), (os.path.expanduser("~") + "/", "~/")):
        if path.startswith(prefix):
            return name + path[len(prefix):]
    return path


# ---------- storage permission + folder picker (Android) ----------
PICK_FOLDER_REQ = 4242


def sdk_int():
    if platform != "android":
        return 0
    from jnius import autoclass  # type: ignore
    return autoclass("android.os.Build$VERSION").SDK_INT


def has_file_access():
    if platform != "android":
        return True
    from jnius import autoclass  # type: ignore
    if sdk_int() >= 30:
        return bool(autoclass("android.os.Environment").isExternalStorageManager())
    from android.permissions import Permission, check_permission  # type: ignore
    return check_permission(Permission.WRITE_EXTERNAL_STORAGE)


def request_file_access():
    if platform != "android":
        return
    from jnius import autoclass  # type: ignore
    if sdk_int() < 30:
        from android.permissions import Permission, request_permissions  # type: ignore
        request_permissions([Permission.WRITE_EXTERNAL_STORAGE, Permission.READ_EXTERNAL_STORAGE])
        return
    Intent = autoclass("android.content.Intent")
    Settings = autoclass("android.provider.Settings")
    Uri = autoclass("android.net.Uri")
    act = autoclass("org.kivy.android.PythonActivity").mActivity
    try:
        intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                        Uri.parse("package:" + act.getPackageName()))
        act.startActivity(intent)
    except Exception:
        act.startActivity(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))


def tree_uri_to_path(uri):
    """content://...tree/primary:Pictures/X  ->  /storage/emulated/0/Pictures/X"""
    from jnius import autoclass  # type: ignore
    doc = autoclass("android.provider.DocumentsContract").getTreeDocumentId(uri)
    volume, _, rel = doc.partition(":")
    base = "/storage/emulated/0" if volume == "primary" else "/storage/" + volume
    return os.path.join(base, rel) if rel else base


def pick_folder(callback):
    """Open Android's folder picker; callback(path or None) runs on the Kivy thread."""
    if platform != "android":
        callback(None)
        return
    from android import activity as android_activity  # type: ignore
    from jnius import autoclass  # type: ignore

    def on_result(request_code, result_code, data):
        if request_code != PICK_FOLDER_REQ:
            return
        android_activity.unbind(on_activity_result=on_result)
        path = None
        try:
            if result_code == -1 and data is not None and data.getData() is not None:
                path = tree_uri_to_path(data.getData())
        except Exception:
            path = None
        Clock.schedule_once(lambda *_: callback(path))

    android_activity.bind(on_activity_result=on_result)
    Intent = autoclass("android.content.Intent")
    act = autoclass("org.kivy.android.PythonActivity").mActivity
    act.startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT_TREE), PICK_FOLDER_REQ)


MEDIA_EXT = tuple("." + e for e in IMAGE_EXT + VIDEO_EXT)


def media_scan(paths):
    """Tell Android about new files so they appear in Gallery / Files."""
    if platform != "android" or not paths:
        return
    from jnius import autoclass  # type: ignore
    scanner = autoclass("android.media.MediaScannerConnection")
    ctx = autoclass("org.kivy.android.PythonActivity").mActivity
    paths = list(paths)
    for i in range(0, len(paths), 200):
        scanner.scanFile(ctx, paths[i:i + 200], None, None)


def media_scan_folder(root):
    found = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(MEDIA_EXT):
                found.append(os.path.join(dirpath, f))
    media_scan(found)
    return len(found)


VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".3gp", ".ts")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp")


def make_thumb(path, out_jpg, size=200):
    """Write a small JPEG preview of a photo or video. Returns True on success."""
    is_video = path.lower().endswith(VIDEO_EXTS)
    if platform == "android":
        try:
            from jnius import autoclass  # type: ignore
            File = autoclass("java.io.File")
            TU = autoclass("android.media.ThumbnailUtils")
            Bitmap = autoclass("android.graphics.Bitmap")
            CompressFormat = autoclass("android.graphics.Bitmap$CompressFormat")
            FileOutputStream = autoclass("java.io.FileOutputStream")
            if sdk_int() >= 29:
                Size = autoclass("android.util.Size")
                bmp = (TU.createVideoThumbnail(File(path), Size(size, size), None) if is_video
                       else TU.createImageThumbnail(File(path), Size(size, size), None))
            else:
                if not is_video:
                    return False
                MINI = autoclass("android.provider.MediaStore$Images$Thumbnails").MINI_KIND
                bmp = TU.createVideoThumbnail(path, MINI)
            if bmp is None:
                return False
            fos = FileOutputStream(out_jpg)
            bmp.compress(CompressFormat.JPEG, 80, fos)
            fos.close()
            return True
        except Exception:
            return False
    try:                                   # desktop: Pillow, PyAV for videos
        from PIL import Image as PILImage
        if is_video:
            import av
            with av.open(path) as c:
                frame = next(c.decode(video=0))
                img = frame.to_image()
        else:
            img = PILImage.open(path)
        img.thumbnail((size, size))
        img.convert("RGB").save(out_jpg, "JPEG", quality=80)
        return True
    except Exception:
        return False


def open_file(path):
    """Open a downloaded file in the phone's viewer/player."""
    if platform != "android":
        return False
    from jnius import autoclass, cast  # type: ignore
    Intent = autoclass("android.content.Intent")
    Uri = autoclass("android.net.Uri")
    File = autoclass("java.io.File")
    StrictMode = autoclass("android.os.StrictMode")
    VmPolicyBuilder = autoclass("android.os.StrictMode$VmPolicy$Builder")
    StrictMode.setVmPolicy(VmPolicyBuilder().build())     # allow a plain file:// link
    ext = os.path.splitext(path)[1].lower()
    mime = ("video/*" if ext in VIDEO_EXTS else "audio/*" if ext in (".m4a", ".mp3", ".opus")
            else "image/*")
    intent = Intent(Intent.ACTION_VIEW)
    intent.setDataAndType(Uri.fromFile(File(path)), mime)
    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_GRANT_READ_URI_PERMISSION)
    act = autoclass("org.kivy.android.PythonActivity").mActivity
    act.startActivity(intent)
    return True


BADGES = {
    "x": ("X", (0.20, 0.21, 0.25, 1), (1, 1, 1, 1)),
    "tiktok": ("TT", (0.93, 0.22, 0.42, 1), (1, 1, 1, 1)),
    "instagram": ("IG", (0.72, 0.25, 0.68, 1), (1, 1, 1, 1)),
    "facebook": ("FB", (0.15, 0.45, 0.93, 1), (1, 1, 1, 1)),
    "snapchat": ("SC", (1.0, 0.84, 0.10, 1), (0.12, 0.10, 0.02, 1)),
    "youtube": ("YT", (0.86, 0.16, 0.16, 1), (1, 1, 1, 1)),
    "web": ("WEB", (0.38, 0.42, 0.50, 1), (1, 1, 1, 1)),
}


def favorite_link(site, name, url):
    """A link that, pasted again, means the same whole profile."""
    if site == "x" and url.endswith("/media"):
        return f"https://x.com/{name}"
    if site == "tiktok" and url.endswith("/posts"):
        return f"https://www.tiktok.com/@{name}"
    if site == "snapchat" and url.startswith("snapfull:"):
        return f"https://www.snapchat.com/add/{name}"
    return url


def start_service(spec, lane=1):
    from jnius import autoclass  # type: ignore
    import json
    act = autoclass("org.kivy.android.PythonActivity").mActivity
    name = "ServiceDownloader" + ("" if lane == 1 else str(lane))
    svc = autoclass("org.personal.xmediadownloader." + name)
    svc.start(act, json.dumps(spec))


MAX_LANES = 3
# desktop: each worker is its own process (the download engine keeps global settings)
LANE_RUNNER = "import json, sys, engine; engine.run_queue(json.loads(sys.argv[1]))"


def split_lanes(jobs, lanes):
    """Spread links over the workers; different sites go side by side first,
    so one site doesn't get several requests from you at the same moment."""
    by_site = {}
    for j in jobs:
        by_site.setdefault(j["site"], []).append(j)
    order = []
    while any(by_site.values()):
        for site in list(by_site):
            if by_site[site]:
                order.append(by_site[site].pop(0))
    return [order[k::lanes] for k in range(lanes)]


def request_notifications():
    if platform == "android" and sdk_int() >= 33:
        from android.permissions import request_permissions  # type: ignore
        request_permissions(["android.permission.POST_NOTIFICATIONS"])


# ---------- Android WebView login helpers ----------
WEBVIEW_UA = [""]   # browser identity used for in-app logins


def login_ua():
    """The in-app browser's identity (also for logins saved by older versions)."""
    if WEBVIEW_UA[0] or platform != "android":
        return WEBVIEW_UA[0]
    try:
        from jnius import autoclass  # type: ignore
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        ua = autoclass("android.webkit.WebSettings").getDefaultUserAgent(act)
        WEBVIEW_UA[0] = ua.replace("; wv", "")
    except Exception:
        pass
    return WEBVIEW_UA[0]
_container = None
_webview = None
_click_ref = None

if platform == "android":
    from android.runnable import run_on_ui_thread  # type: ignore
    from jnius import PythonJavaClass, autoclass, cast, java_method  # type: ignore

    _activity = autoclass("org.kivy.android.PythonActivity").mActivity
    _WebView = autoclass("android.webkit.WebView")
    _WebViewClient = autoclass("android.webkit.WebViewClient")
    _CookieManager = autoclass("android.webkit.CookieManager")
    _LinearLayout = autoclass("android.widget.LinearLayout")
    _LLParams = autoclass("android.widget.LinearLayout$LayoutParams")
    _VGParams = autoclass("android.view.ViewGroup$LayoutParams")
    _AButton = autoclass("android.widget.Button")
    _Color = autoclass("android.graphics.Color")

    class _ClickListener(PythonJavaClass):
        __javainterfaces__ = ["android/view/View$OnClickListener"]
        __javacontext__ = "app"

        def __init__(self, callback):
            super().__init__()
            self.callback = callback

        @java_method("(Landroid/view/View;)V")
        def onClick(self, view):
            self.callback()

    _String = autoclass("java.lang.String")

    def _jtext(text):
        return cast("java.lang.CharSequence", _String(text))

    def _make_webview(url):
        cm = _CookieManager.getInstance()
        cm.setAcceptCookie(True)
        wv = _WebView(_activity)
        st = wv.getSettings()
        st.setJavaScriptEnabled(True)
        st.setDomStorageEnabled(True)
        st.setUserAgentString(st.getUserAgentString().replace("; wv", ""))
        WEBVIEW_UA[0] = st.getUserAgentString()
        cm.setAcceptThirdPartyCookies(wv, True)
        wv.setWebViewClient(_WebViewClient())
        return wv

    @run_on_ui_thread
    def webview_open(url, on_done, on_error):
        global _container, _webview, _click_ref
        if _container is not None:
            return
        # Preferred: login page with a "Done" bar on top.
        try:
            wv = _make_webview(url)
            box = _LinearLayout(_activity)
            box.setOrientation(_LinearLayout.VERTICAL)
            box.setBackgroundColor(_Color.WHITE)
            btn = _AButton(_activity)
            btn.setText(_jtext("Done - back to app"))
            _click_ref = _ClickListener(on_done)
            btn.setOnClickListener(_click_ref)
            box.addView(btn, _LLParams(-1, -2))
            lp = _LLParams(-1, 0)
            lp.weight = 1.0
            box.addView(wv, lp)
            _activity.addContentView(box, _VGParams(-1, -1))
            wv.loadUrl(url)
            _container, _webview = box, wv
            return
        except Exception as e:
            on_error(f"Login bar failed ({e}); opening plain login page.")
        # Fallback: plain full-screen login page (Back returns to the app).
        try:
            wv = _make_webview(url)
            _activity.addContentView(wv, _VGParams(-1, -1))
            wv.loadUrl(url)
            _container, _webview = wv, wv
        except Exception as e:
            on_error(f"Could not open login page: {e}")
            on_done()

    _View = autoclass("android.view.View")
    _close_error = []

    @run_on_ui_thread
    def webview_close():
        global _container, _webview
        if _container is None:
            return
        view, wv = _container, _webview
        _container = _webview = None
        try:
            # getParent() returns a ViewParent; it must be cast to ViewGroup to remove the view.
            parent = view.getParent()
            if parent is not None:
                cast("android.view.ViewGroup", parent).removeView(view)
        except Exception as e:
            _close_error.append(str(e))
            try:
                view.setVisibility(_View.GONE)
            except Exception as e2:
                _close_error.append(str(e2))
        try:
            wv.stopLoading()
            wv.destroy()
        except Exception:
            pass

    SITE_URLS = {
        "x": ("https://x.com", "https://twitter.com", "https://www.x.com"),
        "tiktok": ("https://www.tiktok.com", "https://tiktok.com", "https://m.tiktok.com"),
        "instagram": ("https://www.instagram.com", "https://instagram.com", "https://i.instagram.com"),
        "facebook": ("https://www.facebook.com", "https://m.facebook.com", "https://facebook.com"),
    }

    def webview_cookies(site):
        cm = _CookieManager.getInstance()
        jar = {}
        for u in SITE_URLS[site]:
            for part in (cm.getCookie(u) or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k and v:
                    jar[k] = v
        return jar

else:
    def webview_open(url, on_done, on_error):
        pass

    def webview_close():
        pass

    def webview_cookies(site):
        return {}


class LineWriter(io.TextIOBase):
    """Captures gallery-dl's console output line by line."""
    def __init__(self, callback):
        self.cb, self.buf = callback, ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                self.cb(line.rstrip())
        return len(s)

    def flush(self):
        pass


KV = """
#:import NoTransition kivy.uix.screenmanager.NoTransition
#:import dp kivy.metrics.dp

<GlassCard@BoxLayout>:
    orientation: "vertical"
    padding: dp(16)
    spacing: dp(12)
    size_hint_y: None
    height: self.minimum_height
    canvas.before:
        Color:
            rgba: 1, 1, 1, .07
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(24)]
        Color:
            rgba: 1, 1, 1, .16
        Line:
            rounded_rectangle: (self.x, self.y, self.width, self.height, dp(24))
            width: dp(1)
        Color:
            rgba: 1, 1, 1, .22
        Line:
            points: [self.x + dp(26), self.top - dp(1.5), self.right - dp(26), self.top - dp(1.5)]
            width: dp(1)

<Avatar>:
    size_hint: None, None
    size: dp(32), dp(32)
    pos_hint: {"center_y": .5}
    font_size: "14sp"
    bold: True
    color: 1, 1, 1, .85
    canvas.before:
        Color:
            rgba: 1, 1, 1, .14
        Ellipse:
            pos: self.pos
            size: self.size
    canvas.after:
        Color:
            rgba: (1, 1, 1, 1) if self.photo else (0, 0, 0, 0)
        Ellipse:
            pos: self.pos
            size: self.size
            texture: self.photo

<Thumb>:
    size_hint: None, None
    size: dp(88), dp(88)
    fit_mode: "cover"
    canvas.before:
        Color:
            rgba: 1, 1, 1, .08
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(12)]
    canvas.after:
        Color:
            rgba: 1, 1, 1, .25
        Line:
            rounded_rectangle: (self.x, self.y, self.width, self.height, dp(12))
            width: dp(1)
        Color:
            rgba: (0, 0, 0, .45) if self.video else (0, 0, 0, 0)
        Ellipse:
            pos: self.center_x - dp(15), self.center_y - dp(15)
            size: dp(30), dp(30)
        Color:
            rgba: (1, 1, 1, .95) if self.video else (0, 0, 0, 0)
        Triangle:
            points: [self.center_x - dp(5), self.center_y - dp(8), self.center_x - dp(5), self.center_y + dp(8), self.center_x + dp(9), self.center_y]

<FileTile>:
    size_hint: None, None
    fit_mode: "cover"
    canvas.before:
        Color:
            rgba: 1, 1, 1, .08
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]
    canvas.after:
        Color:
            rgba: (0, 0, 0, .45) if self.video else (0, 0, 0, 0)
        Ellipse:
            pos: self.center_x - dp(14), self.center_y - dp(14)
            size: dp(28), dp(28)
        Color:
            rgba: (1, 1, 1, .95) if self.video else (0, 0, 0, 0)
        Triangle:
            points: [self.center_x - dp(4), self.center_y - dp(7), self.center_x - dp(4), self.center_y + dp(7), self.center_x + dp(8), self.center_y]
        Color:
            rgba: (.40, .85, 1, .35) if self.selected else (0, 0, 0, 0)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]
        Color:
            rgba: (.40, .85, 1, 1) if self.selected else ((1, 1, 1, .7) if self.selecting else (0, 0, 0, 0))
        Line:
            circle: (self.right - dp(14), self.top - dp(14), dp(8))
            width: dp(1.5)
        Ellipse:
            pos: (self.right - dp(20), self.top - dp(20)) if self.selected else (self.right - dp(14), self.top - dp(14))
            size: (dp(12), dp(12)) if self.selected else (0, 0)

<SiteBadge>:
    size_hint: None, None
    size: dp(40), dp(40)
    pos_hint: {"center_y": .5}
    bold: True
    font_size: "13sp"
    color: self.ink
    canvas.before:
        Color:
            rgba: 1, 1, 1, 1
        Ellipse:
            pos: self.pos
            size: self.size
            texture: self.grad
        Color:
            rgba: 1, 1, 1, .20
        Ellipse:
            pos: self.x + self.width * .2, self.y + self.height * .5
            size: self.width * .6, self.height * .36
        Color:
            rgba: 1, 1, 1, .30
        Line:
            ellipse: (self.x + dp(.5), self.y + dp(.5), self.width - dp(1), self.height - dp(1))
            width: dp(1)

<Caption@Label>:
    size_hint_y: None
    height: dp(18)
    font_size: "12sp"
    color: 1, 1, 1, .55
    halign: "left"
    text_size: self.size

<GlassInput>:
    background_normal: "input_bg.png"
    background_active: "input_active.png"
    background_color: 1, 1, 1, 1
    border: [42, 42, 42, 42]
    foreground_color: 1, 1, 1, 1
    hint_text_color: 1, 1, 1, .4
    cursor_color: .40, .85, 1, 1
    selection_color: .40, .85, 1, .35
    multiline: False
    write_tab: False
    size_hint_y: None
    height: dp(50)
    font_size: "15sp"
    padding: [dp(16), dp(14), dp(16), dp(14)] if self.multiline else [dp(16), (self.height - self.line_height) / 2]

<Chip>:
    background_normal: ""
    background_down: ""
    background_color: 0, 0, 0, 0
    font_size: "14sp"
    bold: self.state == "down"
    color: (.04, .07, .14, 1) if self.state == "down" else (1, 1, 1, .8)
    canvas.before:
        Color:
            rgba: (.40, .85, 1, .95) if self.state == "down" else (1, 1, 1, .07)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [self.height / 2]
        Color:
            rgba: (1, 1, 1, 0) if self.state == "down" else (1, 1, 1, .18)
        Line:
            rounded_rectangle: (self.x, self.y, self.width, self.height, self.height / 2)
            width: dp(1)

<GhostButton>:
    background_normal: ""
    background_down: ""
    background_color: 0, 0, 0, 0
    font_size: "14sp"
    color: 1, 1, 1, .95
    canvas.before:
        Color:
            rgba: (1, 1, 1, .20) if self.state == "down" else (1, 1, 1, .10)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(16)]
        Color:
            rgba: 1, 1, 1, .22
        Line:
            rounded_rectangle: (self.x, self.y, self.width, self.height, dp(16))
            width: dp(1)

<PrimaryButton>:
    background_normal: ""
    background_down: ""
    background_disabled_normal: ""
    background_color: 0, 0, 0, 0
    font_size: "17sp"
    bold: True
    color: .03, .07, .12, 1
    disabled_color: .03, .07, .12, .7
    canvas.before:
        Color:
            rgba: (.30, .78, .62, .45) if self.disabled else ((.22, .72, .55, 1) if self.state == "down" else (.30, .86, .64, 1))
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(18)]
        Color:
            rgba: 1, 1, 1, .18
        RoundedRectangle:
            pos: self.x, self.center_y
            size: self.width, self.height / 2
            radius: [dp(18), dp(18), 0, 0]

<AccessPopup>:
    background: ""
    background_color: 0, 0, 0, 0
    overlay_color: 0, 0, 0, .6
    size_hint: .88, None
    height: card.height
    auto_dismiss: False
    GlassCard:
        id: card
        canvas.before:
            Color:
                rgba: .07, .09, .18, .98
            RoundedRectangle:
                pos: self.pos
                size: self.size
                radius: [dp(24)]
        padding: dp(22)
        spacing: dp(14)
        Label:
            text: "Allow file access"
            bold: True
            font_size: "20sp"
            size_hint_y: None
            height: dp(30)
            halign: "left"
            text_size: self.size
        Label:
            text: "To save photos and videos to the folder you choose, allow file access. On the next screen, turn on [b]Allow access to manage all files[/b], then come back."
            markup: True
            font_size: "14sp"
            color: 1, 1, 1, .75
            size_hint_y: None
            text_size: self.width, None
            height: self.texture_size[1]
        PrimaryButton:
            text: "Allow"
            size_hint_y: None
            height: dp(52)
            on_release: root.allow()
        GhostButton:
            text: "Not now"
            size_hint_y: None
            height: dp(46)
            on_release: root.dismiss()

<ConfirmPopup>:
    background: ""
    background_color: 0, 0, 0, 0
    overlay_color: 0, 0, 0, .6
    size_hint: .88, None
    height: ccard.height
    auto_dismiss: True
    GlassCard:
        id: ccard
        canvas.before:
            Color:
                rgba: .07, .09, .18, .98
            RoundedRectangle:
                pos: self.pos
                size: self.size
                radius: [dp(24)]
        padding: dp(22)
        spacing: dp(14)
        Label:
            text: root.title
            bold: True
            font_size: "20sp"
            size_hint_y: None
            height: dp(30)
            halign: "left"
            text_size: self.size
            shorten: True
        Label:
            text: root.message
            markup: True
            font_size: "14sp"
            color: 1, 1, 1, .75
            size_hint_y: None
            text_size: self.width, None
            height: self.texture_size[1]
        BoxLayout:
            size_hint_y: None
            height: dp(50)
            spacing: dp(12)
            GhostButton:
                text: "Cancel"
                on_release: root.dismiss()
            PrimaryButton:
                text: root.ok_text
                on_release: root.confirm()

<TabButton>:
    orientation: "vertical"
    padding: [0, dp(6), 0, dp(4)]
    spacing: dp(2)
    canvas.before:
        Color:
            rgba: (.40, .85, 1, .18) if self.state == "down" else (0, 0, 0, 0)
        RoundedRectangle:
            pos: self.x + dp(2), self.y + dp(1)
            size: self.width - dp(4), self.height - dp(2)
            radius: [dp(16)]
    TabIcon:
        icon: root.icon
        active: root.state == "down"
    Label:
        text: root.text
        font_size: "11sp"
        bold: root.state == "down"
        color: (.55, .9, 1, 1) if root.state == "down" else (1, 1, 1, .6)
        size_hint_y: None
        height: dp(16)
        shorten: True

<ButtonBehaviorBox>:

<Root>:
    Image:
        source: "bg.png"
        allow_stretch: True
        keep_ratio: False
        size_hint: 1, 1
        pos: root.pos

    BoxLayout:
        orientation: "vertical"
        padding: [dp(16), dp(26), dp(16), dp(8)]
        spacing: dp(10)

        BoxLayout:
            size_hint_y: None
            height: dp(48)
            spacing: dp(12)
            Image:
                source: "icon.png"
                size_hint: None, None
                size: dp(44), dp(44)
                pos_hint: {"center_y": .5}
            BoxLayout:
                orientation: "vertical"
                Label:
                    text: "Media Downloader"
                    font_size: "20sp"
                    bold: True
                    halign: "left"
                    valign: "bottom"
                    text_size: self.size
                Label:
                    text: root.tab_title
                    font_size: "13sp"
                    color: 1, 1, 1, .6
                    halign: "left"
                    valign: "top"
                    text_size: self.size

        ScreenManager:
            id: sm
            transition: NoTransition()

            Screen:
                name: "download"
                ScrollView:
                    id: dl_scroll
                    do_scroll_x: False
                    bar_width: dp(3)
                    bar_color: 1, 1, 1, .3
                    BoxLayout:
                        orientation: "vertical"
                        size_hint_y: None
                        height: self.minimum_height
                        spacing: dp(14)
                        padding: [0, 0, 0, dp(8)]
                        GlassCard:
                            Caption:
                                text: "LINKS  (one per line)"
                            GlassInput:
                                id: url
                                multiline: True
                                hint_text: "Paste links (one per line)"
                                height: min(max(dp(50), self.minimum_height), dp(170))
                                on_text: root.update_link_count()
                            BoxLayout:
                                size_hint_y: None
                                height: dp(42)
                                spacing: dp(8)
                                GhostButton:
                                    text: "+ Paste"
                                    on_release: root.paste_link()
                                GhostButton:
                                    text: "Clear"
                                    on_release: root.clear_links()
                                GhostButton:
                                    text: "Save"
                                    on_release: root.save_favorites()
                                Label:
                                    text: ("%d links" % root.link_count) if root.link_count != 1 else "1 link"
                                    font_size: "12sp"
                                    color: 1, 1, 1, .6
                                    size_hint_x: .6
                            Caption:
                                text: "INCLUDE"
                            BoxLayout:
                                size_hint_y: None
                                height: dp(40)
                                spacing: dp(8)
                                Chip:
                                    id: images
                                    on_state: root.save_include()
                                    text: "Photos"
                                    state: "down"
                                Chip:
                                    id: videos
                                    on_state: root.save_include()
                                    text: "Videos"
                                    state: "down"
                                Chip:
                                    id: retweets
                                    on_state: root.save_include()
                                    text: "X reposts"
                            Caption:
                                text: "FROM PROFILES"
                            GridLayout:
                                cols: 4
                                size_hint_y: None
                                height: dp(40)
                                spacing: dp(6)
                                Chip:
                                    id: c_story
                                    text: "Stories"
                                    font_size: "13sp"
                                    state: "down"
                                    on_state: root.save_content()
                                Chip:
                                    id: c_high
                                    text: "Highlights"
                                    font_size: "13sp"
                                    state: "down"
                                    on_state: root.save_content()
                                Chip:
                                    id: c_posts
                                    text: "Posts"
                                    font_size: "13sp"
                                    state: "down"
                                    on_state: root.save_content()
                                Chip:
                                    id: c_spot
                                    text: "Spotlight"
                                    font_size: "13sp"
                                    state: "down"
                                    on_state: root.save_content()
                            Chip:
                                id: c_join
                                text: "Stories as one video (keeps the parts too)"
                                font_size: "13sp"
                                size_hint_y: None
                                height: dp(40)
                                on_state: root.save_content()
                            Widget:
                                size_hint_y: None
                                height: dp(2)
                            PrimaryButton:
                                id: btn
                                text: "Stop" if root.busy else ("Download %d links" % root.link_count if root.link_count > 1 else "Download")
                                size_hint_y: None
                                height: dp(56)
                                on_release: root.start()
                        GlassCard:
                            size_hint_y: None
                            height: dp(300) + (dp(100) if root.thumb_count else 0)
                            spacing: dp(8)
                            BoxLayout:
                                size_hint_y: None
                                height: max(dp(24), status.texture_size[1])
                                spacing: dp(10)
                                Widget:
                                    size_hint: None, None
                                    size: dp(10), dp(10)
                                    pos_hint: {"top": 1} if status.texture_size[1] > dp(30) else {"center_y": .5}
                                    canvas:
                                        Color:
                                            rgba: root.status_color
                                        Ellipse:
                                            pos: self.pos
                                            size: self.size
                                Label:
                                    id: status
                                    text: "Ready"
                                    font_size: "14sp"
                                    halign: "left"
                                    valign: "top"
                                    text_size: self.width, None
                                    max_lines: 3
                                GhostButton:
                                    text: "Copy log"
                                    font_size: "12sp"
                                    size_hint: None, None
                                    size: dp(84), dp(30)
                                    pos_hint: {"top": 1}
                                    on_release: root.copy_log()
                            Widget:
                                size_hint_y: None
                                height: dp(6) if root.progress >= 0 else 0
                                opacity: 1 if root.progress >= 0 else 0
                                canvas:
                                    Color:
                                        rgba: 1, 1, 1, .12
                                    RoundedRectangle:
                                        pos: self.pos
                                        size: self.size
                                        radius: [dp(3)]
                                    Color:
                                        rgba: .40, .85, 1, 1
                                    RoundedRectangle:
                                        pos: self.pos
                                        size: self.width * max(0, min(1, root.progress)), self.height
                                        radius: [dp(3)]
                            ScrollView:
                                id: thumb_scroll
                                do_scroll_y: False
                                size_hint_y: None
                                height: dp(92) if root.thumb_count else 0
                                opacity: 1 if root.thumb_count else 0
                                bar_width: dp(2)
                                bar_color: 1, 1, 1, .3
                                BoxLayout:
                                    id: thumbs
                                    size_hint_x: None
                                    width: self.minimum_width
                                    spacing: dp(8)
                                    padding: [0, dp(2)]
                            ScrollView:
                                id: scroll
                                bar_color: 1, 1, 1, .3
                                bar_width: dp(3)
                                Label:
                                    id: log
                                    size_hint_y: None
                                    height: max(self.texture_size[1], dp(10))
                                    text_size: self.width, None
                                    halign: "left"
                                    valign: "top"
                                    font_size: "12sp"
                                    font_name: "RobotoMono-Regular"
                                    color: 1, 1, 1, .72
                                    line_height: 1.15

            Screen:
                name: "favorites"
                ScrollView:
                    do_scroll_x: False
                    bar_width: dp(3)
                    bar_color: 1, 1, 1, .3
                    BoxLayout:
                        orientation: "vertical"
                        size_hint_y: None
                        height: self.minimum_height
                        spacing: dp(14)
                        padding: [0, 0, 0, dp(8)]
                        GlassCard:
                            BoxLayout:
                                size_hint_y: None
                                height: dp(30)
                                Caption:
                                    text: "FAVORITES"
                                    size_hint_y: 1
                                    valign: "middle"
                                GhostButton:
                                    text: ("Get new for %d shown" % root.fav_shown) if root.fav_query else "Get new from all"
                                    font_size: "12sp"
                                    size_hint_x: None
                                    width: dp(160) if root.fav_query else dp(140)
                                    opacity: 1 if root.fav_count else 0
                                    disabled: not root.fav_count
                                    on_release: root.download_favorites()
                            Label:
                                text: "Save profiles here to get their new posts with one tap."
                                font_size: "12sp"
                                color: 1, 1, 1, .45
                                size_hint_y: None
                                height: 0 if root.fav_count else dp(22)
                                opacity: 0 if root.fav_count else 1
                                text_size: self.width, None
                                halign: "left"
                            BoxLayout:
                                size_hint_y: None
                                height: dp(46) if root.fav_count else 0
                                opacity: 1 if root.fav_count else 0
                                disabled: not root.fav_count
                                spacing: dp(8)
                                GlassInput:
                                    id: fav_search
                                    hint_text: "Search favorites (name or app)"
                                    height: dp(46) if root.fav_count else 0
                                    font_size: "14sp"
                                    on_text: root.set_fav_query(self.text)
                                GhostButton:
                                    text: "×"
                                    font_size: "18sp"
                                    size_hint: None, None
                                    size: (dp(46), dp(46)) if root.fav_query else (0, 0)
                                    opacity: 1 if root.fav_query else 0
                                    disabled: not root.fav_query
                                    on_release: fav_search.text = ""
                            BoxLayout:
                                id: favs
                                orientation: "vertical"
                                size_hint_y: None
                                height: self.minimum_height
                                spacing: dp(6)

            Screen:
                name: "files"
                BoxLayout:
                    orientation: "vertical"
                    spacing: dp(10)
                    BoxLayout:
                        size_hint_y: None
                        height: dp(42)
                        spacing: dp(8)
                        GhostButton:
                            text: "‹ All"
                            size_hint_x: None
                            width: dp(70) if root.files_account else 0
                            opacity: 1 if root.files_account else 0
                            disabled: not root.files_account
                            on_release: root.files_back()
                        Label:
                            text: root.files_title
                            bold: True
                            font_size: "15sp"
                            halign: "left"
                            valign: "middle"
                            text_size: self.size
                            shorten: True
                            shorten_from: "right"
                        GhostButton:
                            text: ("Cancel" if root.files_selecting else "Select")
                            font_size: "13sp"
                            size_hint_x: None
                            width: dp(78) if root.files_account else 0
                            opacity: 1 if root.files_account else 0
                            disabled: not root.files_account
                            on_release: root.files_toggle_select()
                        GhostButton:
                            text: "Refresh"
                            font_size: "13sp"
                            size_hint_x: None
                            width: dp(84) if not root.files_account else 0
                            opacity: 0 if root.files_account else 1
                            disabled: bool(root.files_account)
                            on_release: root.files_refresh()
                    ScrollView:
                        id: files_scroll
                        do_scroll_x: False
                        bar_width: dp(3)
                        bar_color: 1, 1, 1, .3
                        BoxLayout:
                            id: files_box
                            orientation: "vertical"
                            size_hint_y: None
                            height: self.minimum_height
                            spacing: dp(8)
                            padding: [0, 0, 0, dp(8)]
                    BoxLayout:
                        size_hint_y: None
                        height: dp(52) if root.files_selecting else 0
                        opacity: 1 if root.files_selecting else 0
                        disabled: not root.files_selecting
                        spacing: dp(8)
                        GhostButton:
                            text: "Select all"
                            size_hint_x: None
                            width: dp(110)
                            on_release: root.files_select_all()
                        PrimaryButton:
                            text: "Delete %d" % root.files_selected if root.files_selected else "Tap files to select"
                            disabled: not root.files_selected
                            on_release: root.files_delete_selected()

            Screen:
                name: "settings"
                ScrollView:
                    do_scroll_x: False
                    bar_width: dp(3)
                    bar_color: 1, 1, 1, .3
                    BoxLayout:
                        orientation: "vertical"
                        size_hint_y: None
                        height: self.minimum_height
                        spacing: dp(14)
                        padding: [0, 0, 0, dp(8)]
                        GlassCard:
                            Caption:
                                text: "X ACCOUNT"
                            BoxLayout:
                                size_hint_y: None
                                height: dp(50)
                                spacing: dp(8)
                                SiteBadge:
                                    text: "X"
                                    badge: 0.20, 0.21, 0.25, 1
                                    ink: 1, 1, 1, 1
                                GlassInput:
                                    id: token
                                    hint_text: "auth_token"
                                    password: True
                                GhostButton:
                                    id: login_btn
                                    text: "Log in"
                                    size_hint_x: None
                                    width: dp(100)
                                    on_release: root.login("x")
                            Caption:
                                text: "TIKTOK ACCOUNT  (needed for profiles)"
                            BoxLayout:
                                size_hint_y: None
                                height: dp(50)
                                spacing: dp(8)
                                SiteBadge:
                                    text: "TT"
                                    badge: 0.93, 0.22, 0.42, 1
                                    ink: 1, 1, 1, 1
                                GlassInput:
                                    id: tt_state
                                    readonly: True
                                    hint_text: "Logged out"
                                GhostButton:
                                    id: tt_btn
                                    text: "Log in"
                                    size_hint_x: None
                                    width: dp(100)
                                    on_release: root.login("tiktok")
                            Caption:
                                text: "INSTAGRAM ACCOUNT  (required)"
                            BoxLayout:
                                size_hint_y: None
                                height: dp(50)
                                spacing: dp(8)
                                SiteBadge:
                                    text: "IG"
                                    badge: 0.72, 0.25, 0.68, 1
                                    ink: 1, 1, 1, 1
                                GlassInput:
                                    id: ig_state
                                    readonly: True
                                    hint_text: "Logged out"
                                GhostButton:
                                    id: ig_btn
                                    text: "Log in"
                                    size_hint_x: None
                                    width: dp(100)
                                    on_release: root.login("instagram")
                            Caption:
                                text: "FACEBOOK ACCOUNT  (recommended)"
                            BoxLayout:
                                size_hint_y: None
                                height: dp(50)
                                spacing: dp(8)
                                SiteBadge:
                                    text: "FB"
                                    badge: 0.15, 0.45, 0.93, 1
                                    ink: 1, 1, 1, 1
                                GlassInput:
                                    id: fb_state
                                    readonly: True
                                    hint_text: "Logged out"
                                GhostButton:
                                    id: fb_btn
                                    text: "Log in"
                                    size_hint_x: None
                                    width: dp(100)
                                    on_release: root.login("facebook")
                            Caption:
                                text: "SAVE TO"
                            BoxLayout:
                                size_hint_y: None
                                height: dp(50)
                                spacing: dp(8)
                                GlassInput:
                                    id: folder
                                    readonly: True
                                    hint_text: "Download/X-media"
                                GhostButton:
                                    text: "Change"
                                    size_hint_x: None
                                    width: dp(124)
                                    on_release: root.choose_folder()
                            Caption:
                                text: "ACCOUNTS AT THE SAME TIME"
                            BoxLayout:
                                size_hint_y: None
                                height: dp(40)
                                spacing: dp(8)
                                Chip:
                                    id: par1
                                    text: "1 at a time"
                                    group: "parallel"
                                    allow_no_selection: False
                                    font_size: "13sp"
                                    on_state: root.save_parallel()
                                Chip:
                                    id: par2
                                    text: "2 at once"
                                    group: "parallel"
                                    allow_no_selection: False
                                    font_size: "13sp"
                                    on_state: root.save_parallel()
                                Chip:
                                    id: par3
                                    text: "3 at once"
                                    group: "parallel"
                                    allow_no_selection: False
                                    font_size: "13sp"
                                    state: "down"
                                    on_state: root.save_parallel()
                            Caption:
                                text: "FILE NAMES"
                            BoxLayout:
                                size_hint_y: None
                                height: dp(40)
                                spacing: dp(8)
                                Chip:
                                    id: names_new
                                    text: "Profile + time"
                                    group: "names"
                                    allow_no_selection: False
                                    font_size: "13sp"
                                    state: "down"
                                    on_state: root.save_names()
                                Chip:
                                    id: names_orig
                                    text: "As the site names them"
                                    group: "names"
                                    allow_no_selection: False
                                    font_size: "13sp"
                                    on_state: root.save_names()
                            Label:
                                text: "e.g. natgeo 2026-11-06 16-24-05.jpg (the time it was posted). YouTube and websites keep the video title."
                                font_size: "12sp"
                                color: 1, 1, 1, .55
                                size_hint_y: None
                                text_size: self.width, None
                                height: self.texture_size[1]
                                halign: "left"
                            Caption:
                                text: "CHECK FAVORITES AUTOMATICALLY"
                            GridLayout:
                                cols: 4
                                size_hint_y: None
                                height: dp(40)
                                spacing: dp(6)
                                Chip:
                                    id: auto0
                                    text: "Off"
                                    group: "auto"
                                    allow_no_selection: False
                                    font_size: "13sp"
                                    state: "down"
                                    on_state: root.save_auto()
                                Chip:
                                    id: auto6
                                    text: "Every 6 h"
                                    group: "auto"
                                    allow_no_selection: False
                                    font_size: "13sp"
                                    on_state: root.save_auto()
                                Chip:
                                    id: auto12
                                    text: "Every 12 h"
                                    group: "auto"
                                    allow_no_selection: False
                                    font_size: "13sp"
                                    on_state: root.save_auto()
                                Chip:
                                    id: auto24
                                    text: "Daily"
                                    group: "auto"
                                    allow_no_selection: False
                                    font_size: "13sp"
                                    on_state: root.save_auto()
                            Label:
                                id: auto_info
                                text: "Gets new posts from all your favorites in the background and tells you what's new."
                                font_size: "12sp"
                                color: 1, 1, 1, .55
                                size_hint_y: None
                                text_size: self.width, None
                                height: self.texture_size[1]
                                halign: "left"
                        Label:
                            text: "Media Downloader " + root.app_version
                            font_size: "12sp"
                            color: 1, 1, 1, .35
                            size_hint_y: None
                            height: dp(24)

        Label:
            text: root.toast_text
            size_hint_y: None
            height: (self.texture_size[1] + dp(16)) if root.toast_text else 0
            opacity: 1 if root.toast_text else 0
            font_size: "13sp"
            text_size: self.width - dp(28), None
            halign: "center"
            canvas.before:
                Color:
                    rgba: (.40, .85, 1, .22) if root.toast_text else (0, 0, 0, 0)
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [dp(14)]

        # progress while you're on another tab
        ButtonBehaviorBox:
            size_hint_y: None
            height: dp(46) if (root.busy and root.tab != "download") else 0
            opacity: 1 if (root.busy and root.tab != "download") else 0
            disabled: not (root.busy and root.tab != "download")
            orientation: "vertical"
            padding: [dp(14), dp(6)]
            spacing: dp(5)
            on_release: root.show_tab("download")
            canvas.before:
                Color:
                    rgba: 1, 1, 1, .09
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [dp(14)]
            Label:
                text: (root.status_line or "Downloading…") + "   ›"
                font_size: "12sp"
                halign: "left"
                valign: "middle"
                text_size: self.size
                shorten: True
                shorten_from: "right"
            Widget:
                size_hint_y: None
                height: dp(4)
                canvas:
                    Color:
                        rgba: 1, 1, 1, .12
                    RoundedRectangle:
                        pos: self.pos
                        size: self.size
                        radius: [dp(2)]
                    Color:
                        rgba: .40, .85, 1, 1
                    RoundedRectangle:
                        pos: self.pos
                        size: self.width * max(0, min(1, root.progress)), self.height
                        radius: [dp(2)]

        BoxLayout:
            size_hint_y: None
            height: dp(58)
            spacing: dp(6)
            padding: [dp(4), dp(4)]
            canvas.before:
                Color:
                    rgba: .05, .07, .16, .72
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [dp(20)]
                Color:
                    rgba: 1, 1, 1, .14
                Line:
                    rounded_rectangle: (self.x, self.y, self.width, self.height, dp(20))
                    width: dp(1)
            TabButton:
                text: "Download"
                icon: "down"
                state: "down" if root.tab == "download" else "normal"
                on_release: root.show_tab("download")
            TabButton:
                text: "Favorites" + ((" %d" % root.fav_count) if root.fav_count else "")
                icon: "star"
                state: "down" if root.tab == "favorites" else "normal"
                on_release: root.show_tab("favorites")
            TabButton:
                text: "Files"
                icon: "grid"
                state: "down" if root.tab == "files" else "normal"
                on_release: root.show_tab("files")
            TabButton:
                text: "Settings"
                icon: "gear"
                state: "down" if root.tab == "settings" else "normal"
                on_release: root.show_tab("settings")
"""


class AccessPopup(ModalView):
    def allow(self):
        self.dismiss()
        request_file_access()


class ConfirmPopup(ModalView):
    title = StringProperty("")
    message = StringProperty("")
    ok_text = StringProperty("OK")

    def __init__(self, on_ok=None, **kw):
        self._on_ok = on_ok
        super().__init__(**kw)

    def confirm(self):
        self.dismiss()
        if self._on_ok:
            self._on_ok()


def _mix(c, t, amount):
    return [c[i] + (t[i] - c[i]) * amount for i in range(3)] + [1]


class SiteBadge(Label):
    """Round badge with initials: a gradient in the app's colour family, plus a shine."""
    badge = ListProperty([0.3, 0.3, 0.35, 1])
    ink = ListProperty([1, 1, 1, 1])
    grad = ObjectProperty(None, allownone=True)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.on_badge()

    def on_badge(self, *_):
        from kivy.graphics.texture import Texture
        top = _mix(self.badge, (1, 1, 1), .28)       # lighter at the top
        bottom = _mix(self.badge, (0, 0, 0), .30)    # deeper at the bottom
        n = 64
        buf = bytearray()
        for i in range(n):                            # row 0 = bottom of the texture
            t = i / (n - 1)
            buf += bytes(int(255 * (bottom[k] + (top[k] - bottom[k]) * t)) for k in range(3)) + b"\xff"
        tex = Texture.create(size=(1, n), colorfmt="rgba")
        tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
        self.grad = tex


class Avatar(Label):
    photo = ObjectProperty(None, allownone=True)
    source = StringProperty("")

    def on_source(self, *_):
        try:
            from kivy.core.image import Image as CoreImage
            self.photo = CoreImage(self.source, nocache=True).texture if self.source else None
        except Exception:
            self.photo = None


class TapLabel(ButtonBehavior, Label):
    pass


class Thumb(ButtonBehavior, Image):
    path = StringProperty("")
    video = BooleanProperty(False)

    def on_release(self):
        try:
            if not open_file(self.path):
                App.get_running_app().root_widget.set_status(os.path.basename(self.path))
        except Exception as e:
            App.get_running_app().root_widget.set_status(f"Couldn't open the file: {e}")


class FileTile(ButtonBehavior, Image):
    path = StringProperty("")
    video = BooleanProperty(False)
    selected = BooleanProperty(False)
    selecting = BooleanProperty(False)

    def on_release(self):
        root = App.get_running_app().root_widget
        if self.selecting:
            self.selected = not self.selected
            root.files_count_selected()
            return
        try:
            if not open_file(self.path):
                root.set_status(os.path.basename(self.path))
        except Exception as e:
            root.set_status(f"Couldn't open the file: {e}")


def nice_time(ts):
    t = time.localtime(ts)
    today = time.localtime()
    if t.tm_yday == today.tm_yday and t.tm_year == today.tm_year:
        day = "today"
    elif (time.localtime(time.time() + 86400).tm_yday == t.tm_yday):
        day = "tomorrow"
    elif (time.localtime(time.time() - 86400).tm_yday == t.tm_yday):
        day = "yesterday"
    else:
        day = time.strftime("%d %b", t)
    return f"{day} {time.strftime('%H:%M', t)}"


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def scan_downloads(save_dir):
    """Accounts found in the download folder, grouped the way the app saves them."""
    site_of = {v: k for k, v in FOLDERS.items()}
    accounts = []

    def media_in(path):
        files = []
        for dirpath, dirnames, names in os.walk(path):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for n in names:
                if not n.startswith(".") and n.lower().endswith(VIDEO_EXTS + IMAGE_EXTS):
                    files.append(os.path.join(dirpath, n))
        return files

    def add(site, name, path):
        files = media_in(path)
        if not files:
            return
        size = newest = 0
        for f in files:
            try:
                st = os.stat(f)
                size += st.st_size
                newest = max(newest, st.st_mtime)
            except OSError:
                pass
        accounts.append({"site": site, "name": name, "path": path, "count": len(files),
                         "size": size, "newest": newest})

    try:
        entries = sorted(os.listdir(save_dir), key=str.lower)
    except OSError:
        return []
    for e in entries:
        path = os.path.join(save_dir, e)
        if e.startswith(".") or not os.path.isdir(path):
            continue
        if e in site_of:
            for user in sorted(os.listdir(path), key=str.lower):
                up = os.path.join(path, user)
                if not user.startswith(".") and os.path.isdir(up):
                    add(site_of[e], user, up)
        else:
            add("x", e, path)
    return accounts


class GlassInput(TextInput):
    pass


class Chip(ToggleButton):
    @property
    def active(self):
        return self.state == "down"


class GhostButton(Button):
    pass


class PrimaryButton(Button):
    pass


Builder.load_string(KV)

IDLE, BUSY, OK, ERR = (1, 1, 1, .4), (.40, .85, 1, 1), (.30, .86, .64, 1), (.98, .45, .45, 1)


ROW_IDS = {"tiktok": ("tt_state", "tt_btn"), "instagram": ("ig_state", "ig_btn"),
           "facebook": ("fb_state", "fb_btn")}


class ButtonBehaviorBox(ButtonBehavior, BoxLayout):
    pass


class TabIcon(Widget):
    """Small line icons for the tab bar (drawn, so no icon font is needed)."""
    icon = StringProperty("")
    active = BooleanProperty(False)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self.draw, size=self.draw, icon=self.draw, active=self.draw)

    def draw(self, *_):
        from kivy.graphics import Color, Line, Ellipse, RoundedRectangle
        import math
        self.canvas.clear()
        cx, cy = self.center
        r = min(self.width, self.height) * .38
        w = dp(1.6)
        with self.canvas:
            Color(*((.55, .9, 1, 1) if self.active else (1, 1, 1, .62)))
            if self.icon == "down":
                Line(points=[cx, cy + r, cx, cy - r * .55], width=w, cap="round")
                Line(points=[cx - r * .55, cy, cx, cy - r * .55, cx + r * .55, cy],
                     width=w, cap="round", joint="round")
                Line(points=[cx - r * .8, cy - r, cx + r * .8, cy - r], width=w, cap="round")
            elif self.icon == "star":
                pts = []
                for i in range(10):
                    a = math.pi / 2 + i * math.pi / 5
                    rr = r * (1.05 if i % 2 == 0 else .45)
                    pts += [cx + rr * math.cos(a), cy + rr * math.sin(a)]
                Line(points=pts, width=w, close=True, joint="round")
            elif self.icon == "grid":
                g, sz = r * .18, r * .82
                for dx in (-1, 1):
                    for dy in (-1, 1):
                        x = cx + (g if dx > 0 else -g - sz)
                        y = cy + (g if dy > 0 else -g - sz)
                        Line(rounded_rectangle=(x, y, sz, sz, dp(2)), width=w)
            elif self.icon == "gear":
                Line(circle=(cx, cy, r * .42), width=w)
                for i in range(8):
                    a = i * math.pi / 4
                    Line(points=[cx + r * .62 * math.cos(a), cy + r * .62 * math.sin(a),
                                 cx + r * 1.0 * math.cos(a), cy + r * 1.0 * math.sin(a)],
                         width=w * 1.3, cap="round")


class TabButton(ButtonBehavior, BoxLayout):
    text = StringProperty("")
    icon = StringProperty("")
    state = StringProperty("normal")


class Root(FloatLayout):
    status_color = ListProperty(IDLE)
    busy = BooleanProperty(False)
    progress = NumericProperty(-1)
    link_count = NumericProperty(0)
    thumb_count = NumericProperty(0)
    fav_count = NumericProperty(0)
    tab = StringProperty("download")
    tab_title = StringProperty("Save from X, TikTok, Snapchat & more")
    status_line = StringProperty("")
    files_account = StringProperty("")
    files_title = StringProperty("Your downloads")
    files_selecting = BooleanProperty(False)
    files_selected = NumericProperty(0)
    app_version = StringProperty(APP_VERSION)
    toast_text = StringProperty("")
    fav_shown = NumericProperty(0)
    fav_query = StringProperty("")

    def __init__(self, store, **kw):
        super().__init__(**kw)
        self.store = store
        self.running = False
        self.count = 0
        ids = self.ids
        self.url, self.token, self.login_btn = ids.url, ids.token, ids.login_btn
        self.images, self.videos, self.retweets = ids.images, ids.videos, ids.retweets
        self.btn, self.status, self.log, self.scroll = ids.btn, ids.status, ids.log, ids.scroll
        self.status.bind(text=lambda _w, t: setattr(self, "status_line", t.split("\n")[0]))
        saved = store.get("auth")["token"] if store.exists("auth") else ""
        self.token.text = saved
        if saved:
            self.login_btn.text = "Signed in"
        self._login_active = False
        self._login_done = threading.Event()
        for site, (state_id, btn_id) in ROW_IDS.items():
            if store.exists(site):
                self.ids[state_id].text = "Signed in"
                self.ids[btn_id].text = "Sign out"
        self.restore_content()
        self._avatar_widgets = {}
        self._avatar_failed = set()
        self.refresh_favorites()
        self._job_poll = None
        self._log_pos = 0
        self.save_dir = store.get("folder")["path"] if store.exists("folder") else download_root()
        self.ids.folder.text = pretty_path(self.save_dir)
        # Make files from earlier downloads visible in Gallery.
        threading.Thread(target=self._initial_scan, daemon=True).start()

    def _initial_scan(self):
        try:
            root = self.save_dir
            if has_file_access() and os.path.isdir(root):
                n = media_scan_folder(root)
                if n:
                    self.write(f"{n} saved files are now visible in Gallery.")
        except Exception as e:
            self.write(f"Gallery scan issue: {e}")

    # ---- link box ----
    def paste_link(self):
        try:
            from kivy.core.clipboard import Clipboard
            clip = (Clipboard.paste() or "").strip()
        except Exception:
            clip = ""
        if not clip:
            self.set_status("Clipboard is empty. Copy a link first.")
            return
        new, _ = parse_links(clip)
        have = {l[2] for l in parse_links(self.url.text)[0]}
        if new and all(l[2] in have for l in new):
            self.set_status("That link is already in the list.")
            return
        self.url.text = (self.url.text.rstrip() + "\n" + clip) if self.url.text.strip() else clip
        self.url.cursor = (0, len(self.url._lines) - 1)

    def copy_log(self):
        """Copy the full log of the last download, for sharing when something fails."""
        lines = []
        for d in self.lanes:
            try:
                with open(engine.paths(d)[1], encoding="utf-8") as f:
                    lines += f.read().splitlines()
            except OSError:
                pass
        if not lines:
            lines = self.log.text.splitlines()
        # keep every meaningful line; only the file lists ("+ "/"skip ") are trimmed
        lists = [i for i, l in enumerate(lines) if l.startswith(("+ ", "skip "))]
        drop = set(lists[:-30])
        lines = [l for i, l in enumerate(lines) if i not in drop]
        if drop:
            lines.insert(0, f"({len(drop)} file names not shown)")
        try:
            job = engine.read_status(self.lanes[-1]) or {}
        except Exception:
            job = {}
        head = [f"Media Downloader {APP_VERSION}", f"Status: {self.status.text}"]
        if job.get("status"):
            head.append(f"Last download: {job.get('state', '')} · {job['status']}")
        text = "\n".join(head + lines)
        if len(text) > 15000:                       # long pastes get cut off in chat
            text = "\n".join(head) + "\n…\n" + text[-14000:]
        # never share login secrets
        text = re.sub(r"(auth_token|sessionid|c_user|xs|csrftoken|sid_tt|sid_guard|msToken)=\S+",
                      r"\1=<hidden>", text)
        try:
            from kivy.core.clipboard import Clipboard
            Clipboard.copy(text)
            self.set_status("Log copied. Paste it in the chat.")
        except Exception as e:
            self.set_status(f"Couldn't copy the log: {e}")

    CONTENT_IDS = {"stories": "c_story", "highlights": "c_high", "posts": "c_posts",
                   "spotlight": "c_spot", "join": "c_join"}

    def content(self):
        return {k for k, i in self.CONTENT_IDS.items() if self.ids[i].state == "down"}

    def save_content(self):
        if getattr(self, "_restoring", False) or not hasattr(self, "store"):
            return
        self.store.put("content", picked=sorted(self.content()))

    def save_include(self):
        if getattr(self, "_restoring", False) or not hasattr(self, "store"):
            return
        self.store.put("include", photos=self.images.active, videos=self.videos.active,
                       reposts=self.retweets.active)

    # ---- automatic favorites checks ----
    AUTO_IDS = {0: "auto0", 6: "auto6", 12: "auto12", 24: "auto24"}

    def auto_hours(self):
        return next((h for h, i in self.AUTO_IDS.items() if self.ids[i].state == "down"), 0)

    def save_auto(self):
        if getattr(self, "_restoring", False) or not hasattr(self, "store"):
            return
        hours = self.auto_hours()
        self.store.put("auto", hours=hours)
        self.write_auto_spec()
        self.arm_auto(first=True)
        if hours:
            self.request_auto_permission()
        self.update_auto_info()

    def write_auto_spec(self):
        """Save the favorites as ready-made jobs for the background check."""
        import autocheck
        data_dir = App.get_running_app().user_data_dir
        path = autocheck.spec_path(data_dir)
        data = autocheck.load(path)
        data["hours"] = self.auto_hours()
        links, _ = parse_links("\n".join(f["link"] for f in self._favorites()))
        # leave out sites that need a login you don't have yet
        links = [l for l in links if not (l[0] == "instagram" and not self.store.exists("instagram"))]
        jobs, problem = self.build_jobs(links) if links else ([], None)
        sdir = autocheck.status_dir(data_dir)
        for i, j in enumerate(jobs, 1):
            j["pos"] = [i, len(jobs)]
            j["status_dir"] = sdir
        data["queue"] = {"jobs": [] if problem else jobs, "status_dir": sdir}
        try:
            autocheck.save(path, data)
        except OSError:
            pass

    def arm_auto(self, first=False):
        """Make sure the alarm is set (alarms are cleared when the phone restarts)."""
        if platform != "android":
            return
        import autocheck
        data_dir = App.get_running_app().user_data_dir
        path = autocheck.spec_path(data_dir)
        hours = self.auto_hours()
        try:
            if not hours:
                autocheck.schedule(self._activity(), path, 0)
                return
            nxt = autocheck.load(path).get("next") or 0
            now = time.time()
            if first or not nxt:
                delay = hours * 3600
            elif nxt < now:                         # missed (phone was off): check soon
                delay = 120
            else:
                delay = min(nxt - now, hours * 3600)
            autocheck.schedule(self._activity(), path, hours, first_in=delay)
        except Exception as e:
            self.write(f"Couldn't set the automatic check: {e}")

    def _activity(self):
        from jnius import autoclass  # type: ignore
        return autoclass("org.kivy.android.PythonActivity").mActivity

    def request_auto_permission(self):
        """Android 12: exact timing needs a one-time allow in Settings (13+ allows it)."""
        if platform != "android":
            return
        try:
            from jnius import autoclass  # type: ignore
            Build = autoclass("android.os.Build$VERSION")
            if Build.SDK_INT < 31:
                return
            Context = autoclass("android.content.Context")
            act = self._activity()
            am = act.getSystemService(Context.ALARM_SERVICE)
            if am.canScheduleExactAlarms():
                return
            Intent = autoclass("android.content.Intent")
            Settings = autoclass("android.provider.Settings")
            Uri = autoclass("android.net.Uri")
            act.startActivity(Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                                     Uri.parse("package:" + act.getPackageName())))
        except Exception:
            pass

    def update_auto_info(self):
        import autocheck
        data = autocheck.load(autocheck.spec_path(App.get_running_app().user_data_dir))
        hours = self.auto_hours()
        lines = []
        if not hours:
            lines.append("Off. Turn on to get new posts from all your favorites in the "
                         "background; you'll get a notification when there's something new.")
        else:
            n = len((data.get("queue") or {}).get("jobs") or [])
            lines.append(f"Checks {n} favorite{'s' if n != 1 else ''} "
                         f"{'daily' if hours == 24 else f'every {hours} hours'}.")
            if data.get("next"):
                lines.append("Next check: " + nice_time(data["next"]))
        last = data.get("last")
        if last:
            new = last.get("new", 0)
            lines.append(f"Last check: {nice_time(last['time'])} · "
                         f"{new} new file{'s' if new != 1 else ''}"
                         + (" · some had problems" if last.get("result") == "error" else ""))
        self.ids.auto_info.text = "\n".join(lines)

    def rename_files(self):
        return self.ids.names_new.state == "down"

    def save_names(self):
        if getattr(self, "_restoring", False) or not hasattr(self, "store"):
            return
        self.store.put("names", rename=self.rename_files())

    def parallel(self):
        return 3 if self.ids.par3.state == "down" else 2 if self.ids.par2.state == "down" else 1

    def save_parallel(self):
        if getattr(self, "_restoring", False) or not hasattr(self, "store"):
            return
        self.store.put("parallel", n=self.parallel())

    def restore_content(self):
        self._restoring = True
        if self.store.exists("include"):
            inc = self.store.get("include")
            self.ids.images.state = "down" if inc.get("photos", True) else "normal"
            self.ids.videos.state = "down" if inc.get("videos", True) else "normal"
            self.ids.retweets.state = "down" if inc.get("reposts", False) else "normal"
        rename = self.store.get("names").get("rename", True) if self.store.exists("names") else True
        self.ids.names_new.state = "down" if rename else "normal"
        self.ids.names_orig.state = "normal" if rename else "down"
        hours = self.store.get("auto").get("hours", 0) if self.store.exists("auto") else 0
        for h, i in self.AUTO_IDS.items():
            self.ids[i].state = "down" if h == hours else "normal"
        self._restoring = False
        if self.store.exists("parallel"):
            n = self.store.get("parallel").get("n", 3)
            self._restoring = True
            for k in (1, 2, 3):
                self.ids[f"par{k}"].state = "down" if k == n else "normal"
            self._restoring = False
        if not self.store.exists("content"):
            return
        picked = set(self.store.get("content")["picked"])
        self._restoring = True
        for k, i in self.CONTENT_IDS.items():
            self.ids[i].state = "down" if k in picked else "normal"
        self._restoring = False

    # ---- favorites ----
    def _favorites(self):
        return list(self.store.get("favorites")["items"]) if self.store.exists("favorites") else []

    def save_favorites(self):
        links, _ = parse_links(self.url.text)
        if not links:
            self.set_status("Paste a profile link first, then tap Save.")
            return
        favs = self._favorites()
        have = {f["link"] for f in favs}
        added = 0
        for site, name, url in links:
            link = favorite_link(site, name, url)
            if link in have:
                continue
            label = name if name not in ("videos", "photos", "posts", "shared", "playlist",
                                         "albums", "highlights") else link.split("//")[-1][:40]
            favs.append({"site": site, "name": label, "link": link})
            have.add(link)
            added += 1
        self.store.put("favorites", items=favs)
        self.refresh_favorites()
        self.write_auto_spec()
        self.set_status(f"Saved {added} to favorites." if added else "Already in your favorites.")
        self.set_state(OK if added else IDLE)

    def confirm_remove_favorite(self, fav):
        site = SITE_NAMES.get(fav.get("site"), "")
        where = f" ({site})" if site else ""
        ConfirmPopup(title="Remove from favorites?",
                     message=f"[b]{escape_markup(fav['name'])}[/b]{where} will be removed from "
                             "your favorites. Files you already downloaded stay on your phone.",
                     ok_text="Remove",
                     on_ok=lambda: self.remove_favorite(fav["link"])).open()

    def remove_favorite(self, link):
        favs = [f for f in self._favorites() if f["link"] != link]
        self.store.put("favorites", items=favs)
        self.refresh_favorites()
        self.write_auto_spec()
        self.set_status("Removed from favorites.")

    def download_favorite(self, link):
        if self.busy:
            self.set_status("A download is running. Wait for it to finish, or tap Stop first.")
            return
        self.start(text=link)

    def set_fav_query(self, text):
        self.fav_query = text.strip()
        ev = getattr(self, "_fav_search_ev", None)
        if ev:
            ev.cancel()
        self._fav_search_ev = Clock.schedule_once(lambda *_: self.refresh_favorites(), 0.15)

    def _fav_matches(self, f, title=None):
        q = self.fav_query.lower()
        if not q:
            return True
        title = title or dict(self.GROUP_ORDER).get(f["site"], "Websites")
        # name anywhere; app name from its start ("tik" finds TikTok, "na" doesn't find Snapchat)
        return q in f["name"].lower() or title.lower().startswith(q)

    def download_favorites(self):
        favs = [f for f in self._favorites() if self._fav_matches(f)]
        if not favs:
            return
        if self.busy:
            self.set_status("A download is running. Wait for it to finish, or tap Stop first.")
            return
        self.start(text="\n".join(f["link"] for f in favs))

    GROUP_ORDER = [("snapchat", "Snapchat"), ("instagram", "Instagram"), ("tiktok", "TikTok"),
                   ("youtube", "YouTube"), ("x", "X"), ("facebook", "Facebook"),
                   ("web", "Websites")]

    def _collapsed(self):
        return set(self.store.get("fav_groups")["collapsed"]) if self.store.exists("fav_groups") else set()

    def toggle_group(self, site):
        c = self._collapsed()
        c.symmetric_difference_update({site})
        self.store.put("fav_groups", collapsed=sorted(c))
        self.refresh_favorites()

    def download_group(self, site):
        links = [f["link"] for f in self._favorites()
                 if f["site"] == site and self._fav_matches(f)]
        if not links:
            return
        if self.busy:
            self.set_status("A download is running. Wait for it to finish, or tap Stop first.")
            return
        self.start(text="\n".join(links))

    # ---- profile photos for favorites ----
    def _avatar_path(self, link):
        import hashlib
        d = os.path.join(App.get_running_app().user_data_dir, "avatars")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, hashlib.md5(link.encode("utf-8")).hexdigest() + ".jpg")

    def _site_cookies(self, site):
        if site == "x":
            return {"auth_token": self.store.get("auth")["token"]} if self.store.exists("auth") else None
        if site in ROW_IDS and self.store.exists(site):
            return self.store.get(site)["cookies"]
        return None

    def fetch_avatars(self, *_):
        """Fetch missing/old profile photos one at a time, only while idle."""
        if getattr(self, "_avatar_thread", None) and self._avatar_thread.is_alive():
            return
        import avatars
        todo = [f for f in self._favorites()
                if f["site"] in ("x", "instagram", "tiktok", "snapchat", "facebook", "youtube")
                and avatars.needs_refresh(self._avatar_path(f["link"]))
                and f["link"] not in self._avatar_failed]
        if not todo or self.busy:
            return

        def work():
            for f in todo:
                if self.busy:
                    break
                path = self._avatar_path(f["link"])
                try:
                    name = f["name"] if f["site"] != "facebook" else f["link"].split("/")[3]
                    ok = avatars.fetch(f["site"], name, f["link"], path, self._site_cookies(f["site"]))
                except Exception:
                    ok = False
                if ok:
                    Clock.schedule_once(lambda *_, link=f["link"], p=path: self._show_avatar(link, p))
                else:
                    self._avatar_failed.add(f["link"])
            if platform == "android":
                try:
                    from jnius import detach  # type: ignore
                    detach()
                except Exception:
                    pass

        self._avatar_thread = threading.Thread(target=work, daemon=True)
        self._avatar_thread.start()

    def _show_avatar(self, link, path):
        w = self._avatar_widgets.get(link)
        if w is not None:
            w.source = ""
            w.source = path

    def refresh_favorites(self):
        from kivy.factory import Factory
        box = self.ids.favs
        box.clear_widgets()
        self._avatar_widgets = {}
        favs = self._favorites()
        collapsed = self._collapsed()
        known = {k for k, _ in self.GROUP_ORDER}
        searching = bool(self.fav_query)
        shown = 0
        for site, title in self.GROUP_ORDER:
            group = [f for f in favs if (f["site"] if f["site"] in known else "web") == site]
            members = [f for f in group if self._fav_matches(f, title)]
            if not members:
                continue
            shown += len(members)
            open_ = searching or site not in collapsed      # show matches even in closed groups
            text, bg, ink = BADGES.get(site, BADGES["web"])

            # ---- group header
            head = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
            badge = Factory.SiteBadge(text=text, badge=bg, ink=ink)
            badge.size = (dp(36), dp(36))
            badge.font_size = "11sp"
            head.add_widget(badge)
            count = f"{len(members)} of {len(group)}" if len(members) != len(group) else len(members)
            name = TapLabel(text=f"[b]{title}[/b] ({count})", markup=True,
                            halign="left", valign="middle", font_size="14sp",
                            shorten=True, shorten_from="right")
            name.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            name.bind(on_release=lambda _w, s=site: self.toggle_group(s))
            head.add_widget(name)
            get = GhostButton(text="Get new", size_hint_x=None, width=dp(78), font_size="13sp")
            get.bind(on_release=lambda _b, s=site: self.download_group(s))
            head.add_widget(get)
            tog = GhostButton(text="–" if open_ else "+", size_hint_x=None, width=dp(36),
                              font_size="20sp")
            tog.bind(on_release=lambda _b, s=site: self.toggle_group(s))
            head.add_widget(tog)
            box.add_widget(head)
            if not open_:
                continue

            # ---- profiles in the group (indented)
            for f in members:
                row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(10),
                                padding=[dp(4), 0, 0, 0])
                av = Avatar(text=(f["name"][:1] or "?").upper())
                pic = self._avatar_path(f["link"])
                if os.path.exists(pic):
                    av.source = pic
                self._avatar_widgets[f["link"]] = av
                row.add_widget(av)
                label = Label(text=f["name"], halign="left", valign="middle", font_size="14sp",
                              color=(1, 1, 1, .9), shorten=True, shorten_from="right")
                label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
                row.add_widget(label)
                g = GhostButton(text="Get new", size_hint_x=None, width=dp(78), font_size="12sp")
                g.bind(on_release=lambda _b, link=f["link"]: self.download_favorite(link))
                row.add_widget(g)
                rm = GhostButton(text="×", size_hint_x=None, width=dp(36), font_size="18sp")
                rm.bind(on_release=lambda _b, f=f: self.confirm_remove_favorite(f))
                row.add_widget(rm)
                box.add_widget(row)
        if searching and not shown:
            box.add_widget(Label(text=f"No favorites match “{escape_markup(self.fav_query)}”",
                                 font_size="13sp", color=(1, 1, 1, .55), size_hint_y=None,
                                 height=dp(36), halign="left", valign="middle",
                                 text_size=(self.width * .8, dp(36))))
        self.fav_shown = shown
        self.fav_count = len(favs)
        Clock.schedule_once(self.fetch_avatars, 1)

    def update_link_count(self):
        self.link_count = len(parse_links(self.url.text)[0])

    def clear_links(self):
        self.url.text = ""
        self.url.focus = True

    # kept for older callers
    def paste_or_clear(self):
        if self.url.text:
            self.clear_links()
        else:
            self.paste_link()

    # ---- save location ----
    def ensure_access(self):
        if has_file_access():
            return True
        AccessPopup().open()
        return False

    def choose_folder(self):
        if not self.ensure_access():
            return
        pick_folder(self._folder_chosen)

    def _folder_chosen(self, path):
        if not path:
            self.set_status("Folder not changed.")
            return
        try:
            os.makedirs(path, exist_ok=True)
            test = os.path.join(path, ".write_test")
            open(test, "w").close()
            os.remove(test)
        except Exception as e:
            self.set_status("Can't save to that folder. Pick another.")
            self.set_state(ERR)
            self.write(f"Folder error: {e}")
            return
        self.save_dir = path
        self.store.put("folder", path=path)
        self.ids.folder.text = pretty_path(path)
        self.set_status("Save folder set.")
        self.set_state(OK)

    def set_state(self, color):
        Clock.schedule_once(lambda *_: setattr(self, "status_color", color))

    def write(self, line):
        def _do(*_):
            lines = (self.log.text + line + "\n").splitlines()[-60:]
            self.log.text = "\n".join(lines) + "\n"
            self.scroll.scroll_y = 0
        Clock.schedule_once(_do)

    def set_status(self, text):
        def apply(*_):
            self.status.text = text
            if self.tab != "download" and not self.busy:
                self.toast(text)
        Clock.schedule_once(apply)

    def toast(self, text, seconds=3.5):
        self.toast_text = text
        ev = getattr(self, "_toast_ev", None)
        if ev:
            ev.cancel()
        self._toast_ev = Clock.schedule_once(lambda *_: setattr(self, "toast_text", ""), seconds)

    # ---- in-app X login (Android WebView) ----
    LOGIN = {
        "x": ("https://x.com/i/flow/login", "auth_token", "X"),
        "tiktok": ("https://www.tiktok.com/login/phone-or-email/phone", "sessionid", "TikTok"),
        "instagram": ("https://www.instagram.com/accounts/login/", "sessionid", "Instagram"),
        "facebook": ("https://m.facebook.com/login/", "c_user", "Facebook"),
    }

    def login(self, site="x"):
        if platform != "android":
            self.set_status("In-app login works on Android only.")
            return
        if self._login_active:
            self._login_done.set()
            self.set_status("Login reset. Tap Log in again.")
            return
        if site in ROW_IDS and self.store.exists(site):
            self.store.delete(site)
            state_id, btn_id = ROW_IDS[site]
            self.ids[state_id].text = ""
            self.ids[btn_id].text = "Log in"
            self.set_status(f"Signed out of {SITE_NAMES[site]} in this app.")
            self.set_state(IDLE)
            return
        url, _, name = self.LOGIN[site]
        self._login_active = True
        self._login_done = threading.Event()
        webview_open(url, self._login_done.set, self.write)
        how = ("phone number + SMS code, or username/email + password (Google won't work here)"
               if site == "tiktok" else "username/email and password (Google won't work here)")
        self.set_status(f"Log in to {name} with {how}.")
        threading.Thread(target=self._watch_login, args=(site,), daemon=True).start()

    def _watch_login(self, site):
        # Runs off the UI threads so it keeps working while the login page is on top.
        _, key, name = self.LOGIN[site]
        jar, deadline = {}, time.time() + 900
        while time.time() < deadline:
            try:
                jar = webview_cookies(site)
            except Exception as e:
                self.write(f"Login check error: {e}")
            if jar.get(key) or self._login_done.is_set():
                break
            time.sleep(1)
        if not jar.get(key):
            try:
                jar = webview_cookies(site)
            except Exception:
                pass
        webview_close()
        self._login_active = False
        if platform == "android":
            def _report(*_):
                while _close_error:
                    self.write("Close issue: " + _close_error.pop(0))
            Clock.schedule_once(_report, 1.5)
        if not jar.get(key):
            self.set_status(f"{name} login not detected. Try again.")
            self.set_state(ERR)
            self.write(f"No {key} found. Cookies seen: {', '.join(jar) or 'none'}")
            return
        if site == "x":
            token = jar[key]
            self.store.put("auth", token=token)
            def _ok(*_):
                self.token.text = token
                self.login_btn.text = "Signed in"
        else:
            self.store.put(site, cookies=jar, ua=WEBVIEW_UA[0])
            state_id, btn_id = ROW_IDS[site]
            def _ok(*_):
                self.ids[state_id].text = "Signed in"
                self.ids[btn_id].text = "Sign out"
        Clock.schedule_once(_ok)
        self._avatar_failed.clear()          # retry profile photos with the new login
        Clock.schedule_once(self.fetch_avatars, 2)
        self.set_status(f"Logged in to {name}. Paste a link and tap Download.")
        self.set_state(OK)

    # ---- Files tab (downloads browser) ----
    FILES_PAGE = 60

    def files_open(self):
        if self.files_account:
            return
        if getattr(self, "_files_cache", None) is None or \
                time.time() - getattr(self, "_files_cache_t", 0) > 30:
            self.files_refresh()
        else:
            self._files_render_accounts(self._files_cache)

    def files_refresh(self):
        box = self.ids.files_box
        box.clear_widgets()
        box.add_widget(Label(text="Looking through your downloads…", font_size="13sp",
                             color=(1, 1, 1, .6), size_hint_y=None, height=dp(40)))
        save_dir = self.save_dir

        def work():
            accounts = scan_downloads(save_dir)
            def done(*_):
                self._files_cache, self._files_cache_t = accounts, time.time()
                if self.tab == "files" and not self.files_account:
                    self._files_render_accounts(accounts)
            Clock.schedule_once(done)
        threading.Thread(target=work, daemon=True).start()

    def _files_render_accounts(self, accounts):
        from kivy.factory import Factory
        self.files_title = "Your downloads"
        box = self.ids.files_box
        box.clear_widgets()
        if not accounts:
            box.add_widget(Label(
                text="Nothing downloaded yet in\n" + pretty_path(self.save_dir),
                font_size="13sp", color=(1, 1, 1, .6), size_hint_y=None, height=dp(60),
                halign="center"))
            return
        total = sum(a["count"] for a in accounts)
        size = sum(a["size"] for a in accounts)
        self.files_title = f"Your downloads · {total} files · {human_size(size)}"
        for site, title in self.GROUP_ORDER:
            members = [a for a in accounts if (a["site"] if a["site"] in dict(self.GROUP_ORDER) else "web") == site]
            if not members:
                continue
            members.sort(key=lambda a: -a["newest"])
            text, bg, ink = BADGES.get(site, BADGES["web"])
            head = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
            badge = Factory.SiteBadge(text=text, badge=bg, ink=ink)
            badge.size = (dp(32), dp(32))
            badge.font_size = "10sp"
            head.add_widget(badge)
            lab = Label(text=f"[b]{title}[/b]  ({len(members)})", markup=True, font_size="14sp",
                        halign="left", valign="middle")
            lab.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            head.add_widget(lab)
            box.add_widget(head)
            for a in members:
                row = ButtonBehaviorBox(size_hint_y=None, height=dp(54), spacing=dp(10),
                                        padding=[dp(10), dp(6)])
                with row.canvas.before:
                    from kivy.graphics import Color, RoundedRectangle
                    Color(1, 1, 1, .06)
                    rr = RoundedRectangle(radius=[dp(14)])
                row.bind(pos=lambda w, *_, r=rr: setattr(r, "pos", w.pos),
                         size=lambda w, *_, r=rr: setattr(r, "size", w.size))
                av = Avatar(text=(a["name"][:1] or "?").upper())
                fav = next((f for f in self._favorites()
                            if f["name"].lower() == a["name"].lower() and f["site"] == a["site"]), None)
                if fav and os.path.exists(self._avatar_path(fav["link"])):
                    av.source = self._avatar_path(fav["link"])
                row.add_widget(av)
                info = Label(text=f"{escape_markup(a['name'])}\n[size=12sp][color=ffffff99]"
                                  f"{a['count']} files · {human_size(a['size'])}[/color][/size]",
                             markup=True, font_size="14sp", halign="left", valign="middle",
                             max_lines=2)
                info.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
                row.add_widget(info)
                row.add_widget(Label(text="›", font_size="22sp", size_hint_x=None, width=dp(20),
                                     color=(1, 1, 1, .5)))
                row.bind(on_release=lambda _w, a=a: self.files_show(a))
                box.add_widget(row)
        self.ids.files_scroll.scroll_y = 1

    def files_show(self, account):
        self._files_acc = account
        self.files_account = account["path"]
        self.files_title = f"{account['name']} · {SITE_NAMES.get(account['site'], '')}"
        self.files_selecting = False
        self.files_selected = 0
        box = self.ids.files_box
        box.clear_widgets()
        box.add_widget(Label(text="Loading…", font_size="13sp", color=(1, 1, 1, .6),
                             size_hint_y=None, height=dp(40)))

        def work():
            files = []
            for dirpath, dirnames, names in os.walk(account["path"]):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for n in names:
                    if not n.startswith(".") and n.lower().endswith(VIDEO_EXTS + IMAGE_EXTS):
                        p = os.path.join(dirpath, n)
                        try:
                            files.append((os.path.getmtime(p), p))
                        except OSError:
                            pass
            files.sort(reverse=True)                       # newest first
            paths = [p for _, p in files]
            Clock.schedule_once(lambda *_: self._files_render_grid(paths))
        threading.Thread(target=work, daemon=True).start()

    def _files_render_grid(self, paths):
        if not self.files_account:
            return
        self._files_paths = paths
        box = self.ids.files_box
        box.clear_widgets()
        if not paths:
            box.add_widget(Label(text="No files here any more.", font_size="13sp",
                                 color=(1, 1, 1, .6), size_hint_y=None, height=dp(40)))
            return
        acc = self._files_acc
        self.files_title = (f"{acc['name']} · {len(paths)} files")
        cols = 3
        tile = (box.width - dp(6) * (cols - 1)) / cols if box.width > 50 else dp(110)
        self._files_grid = GridLayout(cols=cols, spacing=dp(6), size_hint_y=None,
                                      row_default_height=tile, row_force_default=True)
        self._files_grid.bind(minimum_height=self._files_grid.setter("height"))
        box.add_widget(self._files_grid)
        self._files_tile = tile
        self._files_shown = 0
        self._files_more = None
        self._files_add_page()
        self.ids.files_scroll.scroll_y = 1

    def _files_add_page(self, *_):
        box, grid = self.ids.files_box, self._files_grid
        paths = self._files_paths[self._files_shown:self._files_shown + self.FILES_PAGE]
        for p in paths:
            t = FileTile(path=p, video=p.lower().endswith(VIDEO_EXTS), size=(self._files_tile,) * 2,
                         selecting=self.files_selecting)
            grid.add_widget(t)
            self._thumb_queue(p, t)
        self._files_shown += len(paths)
        if self._files_more is not None:
            box.remove_widget(self._files_more)
            self._files_more = None
        left = len(self._files_paths) - self._files_shown
        if left > 0:
            self._files_more = GhostButton(text=f"Show more ({left} left)", size_hint_y=None,
                                           height=dp(46))
            self._files_more.bind(on_release=self._files_add_page)
            box.add_widget(self._files_more)

    def _thumb_queue(self, path, tile):
        import queue
        if getattr(self, "_tq", None) is None:
            self._tq = queue.LifoQueue()           # newest requests first (what's on screen)
            for _ in range(3):
                threading.Thread(target=self._thumb_loop, daemon=True).start()
        self._tq.put((path, tile))

    def _thumb_loop(self):
        while True:
            path, tile = self._tq.get()
            if tile.parent is None:                # left the page already
                continue
            try:
                self._thumb_worker(path, tile)
            except Exception:
                pass

    def files_back(self):
        self.files_account = ""
        self.files_selecting = False
        self.files_selected = 0
        self._files_render_accounts(getattr(self, "_files_cache", None) or [])
        if getattr(self, "_files_dirty", False):
            self._files_dirty = False
            self.files_refresh()

    def _files_tiles(self):
        grid = getattr(self, "_files_grid", None)
        return [w for w in grid.children if isinstance(w, FileTile)] if grid else []

    def files_toggle_select(self):
        self.files_selecting = not self.files_selecting
        for t in self._files_tiles():
            t.selecting = self.files_selecting
            if not self.files_selecting:
                t.selected = False
        self.files_count_selected()

    def files_select_all(self):
        tiles = self._files_tiles()
        on = not all(t.selected for t in tiles)
        for t in tiles:
            t.selected = on
        self.files_count_selected()

    def files_count_selected(self):
        self.files_selected = sum(1 for t in self._files_tiles() if t.selected)

    def files_delete_selected(self):
        paths = [t.path for t in self._files_tiles() if t.selected]
        if not paths:
            return
        n = len(paths)
        ConfirmPopup(
            title=f"Delete {n} file{'s' if n != 1 else ''}?",
            message="They'll be removed from your phone and from Gallery. "
                    "[b]Get new[/b] won't download them again.",
            ok_text="Delete",
            on_ok=lambda: self._files_delete(paths)).open()

    def _files_delete(self, paths):
        gone = []
        for p in paths:
            try:
                os.remove(p)
                gone.append(p)
            except OSError:
                pass
        try:
            media_scan(gone)              # removes them from Gallery too
        except Exception:
            pass
        failed = len(paths) - len(gone)
        self.toast(f"Deleted {len(gone)} file{'s' if len(gone) != 1 else ''}"
                   + (f"; {failed} couldn't be deleted" if failed else "") + ".")
        self._files_dirty = True
        self.files_selecting = False
        self.files_selected = 0
        self._files_render_grid([p for p in self._files_paths if p not in set(gone)])

    TAB_TITLES = {"download": "Save from X, TikTok, Snapchat & more",
                  "favorites": "Saved profiles · one tap for new posts",
                  "files": "Everything you downloaded",
                  "settings": "Accounts, folder and options"}

    def show_tab(self, name):
        self.tab = name
        self.ids.sm.current = name
        self.tab_title = self.TAB_TITLES.get(name, "")
        if name == "files":
            self.files_open()
        elif name == "settings":
            self.update_auto_info()

    def on_back(self):
        if self._login_active:
            self._login_done.set()
            return True
        if self.tab == "files" and self.files_selecting:
            self.files_toggle_select()
            return True
        if self.tab == "files" and self.files_account:
            self.files_back()
            return True
        if self.tab != "download":
            self.show_tab("download")
            return True
        return False

    # ---- downloading (runs in a background service on Android) ----
    @property
    def status_dir(self):
        return os.path.join(App.get_running_app().user_data_dir, "job")

    def lane_dir(self, k):
        return self.status_dir if k == 1 else f"{self.status_dir}{k}"

    @property
    def lanes(self):
        return getattr(self, "_lanes", None) or [self.status_dir]

    def build_jobs(self, links):
        """Turn links into download jobs with the current options.
        Returns (jobs, problem) — problem is a message for the user, or None."""
        if not links:
            return [], "Paste a link (X, TikTok, Snapchat, Instagram, Facebook, YouTube or any website)."
        if not (self.images.active or self.videos.active):
            return [], "Pick Photos and/or Videos."
        token = self.token.text.strip()
        if token:
            self.store.put("auth", token=token)
        if any(l[0] == "instagram" for l in links) and not self.store.exists("instagram"):
            return [], "Log in to Instagram first (Settings → Instagram → Log in)."
        content = self.content()
        if not content and any(l[0] in ("snapchat", "instagram", "tiktok") for l in links):
            return [], "Pick at least one of Stories, Highlights, Posts or Spotlight."
        jobs = []
        for site, user, gurl in links:
            out = output_dir(self.save_dir, site, user)
            if site == "youtube":
                jobs.append({"args": [], "out": out, "out_label": pretty_path(out),
                             "site": site, "user": user, "status_dir": self.status_dir,
                             "yt": {"url": gurl, "audio_only": not self.videos.active}})
                continue
            if site == "web":
                gdl_out = os.path.join(out, "Images")
                jobs.append({
                    # images fallback (main engine) if no video is found
                    "args": ["-d", gdl_out, "--download-archive",
                             os.path.join(gdl_out, ".downloaded.sqlite3"),
                             "-o", "path-restrict=windows", "-o", "path-strip=windows", gurl],
                    "out": out, "out_label": pretty_path(out), "site": site, "user": user,
                    "status_dir": self.status_dir,
                    "yt": {"url": gurl, "audio_only": not self.videos.active,
                           "format": WEB_FORMAT_HINT, "name": user,
                           "folder": "%(extractor_key)s"}})
                continue
            jobs.append({
                "args": build_args(site, gurl, out, token, self.images.active,
                                   self.videos.active, self.retweets.active,
                                   self.store.get(site)["cookies"]
                                   if site in ROW_IDS and self.store.exists(site) else None,
                                   (self.store.get(site).get("ua") or login_ua())
                                   if site in ROW_IDS and self.store.exists(site) else None,
                                   content, rename=self.rename_files()),
                "out": out, "out_label": pretty_path(out), "site": site, "user": user,
                "status_dir": self.status_dir, "rename": self.rename_files(),
                "join_stories": "join" in content and "stories" in content,
            })
        return jobs, None

    def start(self, text=None):
        if self.busy:
            for d in self.lanes:
                engine.request_cancel(d)
            self.set_status("Stopping…")
            return
        links, bad = parse_links(self.url.text if text is None else text)
        jobs, problem = self.build_jobs(links)
        if problem:
            self.set_status(problem)
            self.set_state(ERR)
            return
        if not self.ensure_access():
            self.set_status("Allow file access, then tap Download again.")
            return
        for j in jobs:
            os.makedirs(j["out"], exist_ok=True)
        site, user = links[0][0], links[0][1]
        n_lanes = max(1, min(self.parallel(), len(jobs), MAX_LANES))
        groups = split_lanes(jobs, n_lanes)
        for i, j in enumerate(jobs, 1):
            j["pos"] = [i, len(jobs)]
            j["tag"] = n_lanes > 1
        dirs = [self.lane_dir(k) for k in range(1, n_lanes + 1)]
        specs = []
        for k, (d, group) in enumerate(zip(dirs, groups), 1):
            for j in group:
                j["status_dir"] = d
            specs.append({"jobs": group, "status_dir": d, "lane": k, "lanes": dirs})
        for k in range(1, MAX_LANES + 1):
            d = self.lane_dir(k)
            os.makedirs(d, exist_ok=True)
            for f in engine.paths(d) + (engine.files_path(d),):
                try:
                    os.remove(f)
                except OSError:
                    pass
        self._lanes = dirs
        self._lane_links = [len(g) for g in groups]
        self._started = time.time()
        self._fallback = False
        self.store.put("lanes", dirs=dirs, links=self._lane_links)

        self.log.text = ""
        self._log_pos = {}
        self.clear_thumbs()
        self.progress = -1
        self.busy = True
        self.set_status((f"Downloading from {user}…" if site == "web"
                         else f"Downloading {SITE_NAMES[site]} @{user}…") if len(jobs) == 1
                        else f"Downloading {len(jobs)} links, {n_lanes} at once…" if n_lanes > 1
                        else f"Downloading {len(jobs)} links…")
        self.set_state(BUSY)
        if bad:
            self.write("Skipped (not a supported link): " + ", ".join(bad[:5]))
        if platform == "android":
            try:
                for k, spec in enumerate(specs, 1):
                    start_service(spec, k)
            except Exception as e:
                self.write(f"Background mode unavailable ({e}); downloading in the app.")
                self._fallback = True

                def run_all(specs=specs):
                    for spec in specs:          # one after another inside the app
                        engine.run_queue(spec, scan=media_scan)
                threading.Thread(target=run_all, daemon=True).start()
        elif len(specs) == 1:
            threading.Thread(target=lambda: engine.run_queue(specs[0]), daemon=True).start()
        else:
            import subprocess
            here = os.path.dirname(os.path.abspath(__file__))
            for spec in specs:
                subprocess.Popen([sys.executable, "-c", LANE_RUNNER, json.dumps(spec)], cwd=here)
        self._watch_job()

    def _watch_job(self):
        if self._job_poll is None:
            self._job_poll = Clock.schedule_interval(self._poll_job, 0.5)

    def resume_job(self):
        if self._job_poll is not None:
            return
        saved = self.store.get("lanes") if self.store.exists("lanes") else {}
        self._lanes = saved.get("dirs") or [self.status_dir]
        self._lane_links = saved.get("links") or [1] * len(self._lanes)
        self._started, self._fallback = 0, False
        sts = [engine.read_status(d) for d in self._lanes]
        if any(st and st.get("state") == "running" and time.time() - st.get("beat", 0) < 45
               for st in sts):
            self.busy = True
            self.set_state(BUSY)
            self._log_pos = {}
            self.log.text = ""
            self.clear_thumbs()
            self._watch_job()

    MAX_THUMBS = 30

    def clear_thumbs(self):
        self.ids.thumbs.clear_widgets()
        self.thumb_count = 0
        self._files_pos = {}

    def _poll_files(self):
        if not isinstance(getattr(self, "_files_pos", None), dict):
            self._files_pos = {}
        paths = []
        for d in self.lanes:
            try:
                with open(engine.files_path(d), encoding="utf-8") as f:
                    f.seek(self._files_pos.get(d, 0))
                    chunk = f.read()
                    self._files_pos[d] = f.tell()
            except OSError:
                continue
            paths += [p for p in chunk.splitlines() if p.strip()]
        for p in paths[-self.MAX_THUMBS:]:
            if p.lower().endswith(VIDEO_EXTS + IMAGE_EXTS):
                tile = self._add_thumb(p)          # placeholder now, keeps download order
                threading.Thread(target=self._thumb_worker, args=(p, tile), daemon=True).start()

    def _thumb_worker(self, path, tile):
        cache = os.path.join(App.get_running_app().user_data_dir, "thumbs")
        os.makedirs(cache, exist_ok=True)
        import hashlib
        out = os.path.join(cache, hashlib.md5(path.encode("utf-8")).hexdigest() + ".jpg")
        ok = os.path.exists(out) or make_thumb(path, out)
        if platform == "android":
            try:
                from jnius import detach  # type: ignore
                detach()
            except Exception:
                pass
        src = out if ok else (path if path.lower().endswith(IMAGE_EXTS) else "")
        if src:
            Clock.schedule_once(lambda *_: setattr(tile, "source", src))

    def _add_thumb(self, path):
        box = self.ids.thumbs
        t = Thumb(path=path, video=path.lower().endswith(VIDEO_EXTS))
        box.add_widget(t, index=len(box.children))   # newest on the left
        while len(box.children) > self.MAX_THUMBS:
            box.remove_widget(box.children[0])
        self.thumb_count = len(box.children)
        self.ids.thumb_scroll.scroll_x = 0
        return t

    def _poll_job(self, *_):
        self._poll_files()
        if not isinstance(getattr(self, "_log_pos", None), dict):
            self._log_pos = {}
        for d in self.lanes:
            _, log_path, _ = engine.paths(d)
            try:
                with open(log_path, encoding="utf-8") as f:
                    f.seek(self._log_pos.get(d, 0))
                    chunk = f.read()
                    self._log_pos[d] = f.tell()
                if chunk:
                    lines = (self.log.text + chunk).splitlines()[-60:]
                    self.log.text = "\n".join(lines) + "\n"
                    self.scroll.scroll_y = 0
            except OSError:
                pass

        now = time.time()
        sts = [engine.read_status(d) for d in self.lanes]
        if len(sts) == 1:
            st = sts[0]
            if not st:
                return
            self._show_single(st)
            return

        # several accounts at once: add up what the workers report
        links = sum(self._lane_links)
        done_links = new = problems = 0
        frac = 0.0
        running, waiting, states, lines = 0, 0, [], []
        for st, n in zip(sts, self._lane_links):
            if not st:
                # worker not started yet (or failed to start)
                if self._fallback or now - (self._started or now) < 60:
                    waiting += 1
                else:
                    states.append("error")
                    problems += n
                continue
            state = st.get("state")
            stale = state == "running" and now - st.get("beat", 0) > 45
            if state == "running" and not stale:
                running += 1
                done_links += st.get("done_links") or 0
                new += (st.get("new_before") or 0) + (st.get("count") or 0)
                total, seen = st.get("total") or 0, st.get("seen") or 0
                frac += min(seen / total, 1) if total else 0
                lines.append(st.get("status", ""))
            else:
                states.append("error" if stale else state)
                done_links += st.get("done_links") or n
                new += st.get("count") or 0
                problems += st.get("problems", 1 if state == "error" or stale else 0) or 0
        self.progress = min((done_links + frac) / links, 1) if links else -1
        if running or waiting:
            head = (f"{running} at once · {done_links} of {links} links done · "
                    f"{new} new files")
            self.status.text = "\n".join([head] + lines[:2])
            return
        if "cancelled" in states:
            text, color = f"Stopped. {new} new files", IDLE
        elif problems:
            text, color = f"Finished {links} links, {problems} with problems. {new} new files", ERR
        else:
            text, color = f"Finished all {links} links. {new} new files", OK
        self.status.text = text
        self.status_color = color
        self.progress = 1 if color == OK else -1
        self._end_poll()

    def _show_single(self, st):
        self.status.text = st.get("status", "")
        total, seen = st.get("total") or 0, st.get("seen") or 0
        self.progress = min(seen / total, 1) if total else -1
        state = st.get("state")
        stale = state == "running" and time.time() - st.get("beat", 0) > 45
        if state != "running" or stale:
            if stale:
                self.status.text = "Download stopped unexpectedly. Tap Download to continue."
            self.status_color = OK if state == "done" else (IDLE if state == "cancelled" else ERR)
            if state == "done" and self.progress >= 0:
                self.progress = 1
            elif state != "done":
                self.progress = -1
            self._end_poll()

    def _end_poll(self):
        self.busy = False
        if self._job_poll is not None:
            self._job_poll.cancel()
            self._job_poll = None
        Clock.schedule_once(self.fetch_avatars, 2)


class XMediaApp(App):
    title = "X Media Downloader"

    def build(self):
        Window.softinput_mode = "below_target"
        Window.clearcolor = (9/255, 12/255, 28/255, 1)
        self.root_widget = Root(JsonStore(os.path.join(self.user_data_dir, "settings.json")))
        if platform == "android":
            Clock.schedule_once(lambda *_: self.root_widget.ensure_access(), 1)
            Clock.schedule_once(lambda *_: request_notifications(), 2)
        Clock.schedule_once(lambda *_: self.root_widget.resume_job(), 0.5)
        Clock.schedule_once(lambda *_: (self.root_widget.write_auto_spec(),
                                        self.root_widget.arm_auto()), 3)
        Window.bind(on_keyboard=self._on_key)
        return self.root_widget

    def on_pause(self):
        try:
            self.root_widget.write_auto_spec()       # keep the background check up to date
        except Exception:
            pass
        return True

    def on_stop(self):
        try:
            self.root_widget.write_auto_spec()
        except Exception:
            pass  # keep running while the folder picker or Settings is open

    def on_resume(self):
        self.root_widget.resume_job()
        if has_file_access():
            self.root_widget.set_status("File access allowed.")
            self.root_widget.set_state(OK)
            threading.Thread(target=self.root_widget._initial_scan, daemon=True).start()

    def _on_key(self, window, key, *args):
        if key == 27:  # Android Back
            return self.root_widget.on_back()
        return False


if __name__ == "__main__":
    XMediaApp().run()
