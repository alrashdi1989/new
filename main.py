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
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.storage.jsonstore import JsonStore
from kivy.lang import Builder
from kivy.properties import BooleanProperty, ListProperty, NumericProperty
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.modalview import ModalView
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton
from kivy.utils import platform

import engine

RESERVED = {"home", "explore", "search", "notifications", "messages", "i", "settings",
            "compose", "login", "logout", "signup", "tos", "privacy", "hashtag", "intent"}
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
    return None


def build_args(site, url, out_dir, auth_token, images, videos, retweets, site_cookies=None):
    exts = (IMAGE_EXT if images else ()) + (VIDEO_EXT if videos else ())
    args = [
        "-d", out_dir,
        "--download-archive", os.path.join(out_dir, ".downloaded.sqlite3"),
        "-o", "videos=true",
        # Android's shared storage rejects : ? * " < > | \ in file names
        "-o", "path-restrict=windows",
        "-o", "path-strip=windows",
        "-o", ('directory=["{section}"]' if site == "snapchat" else
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
        if site_cookies:  # the TikTok login is only ever sent to TikTok
            args += ["-o", "cookies=" + json.dumps(site_cookies)]
    elif site == "instagram":
        if is_ig_profile(url):
            args += ["-o", "include=stories,highlights,posts"]
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


SITE_NAMES = {"x": "X", "tiktok": "TikTok", "snapchat": "Snapchat", "instagram": "Instagram"}
FOLDERS = {"tiktok": "TikTok", "snapchat": "Snapchat", "instagram": "Instagram"}


def is_ig_profile(url):
    return re.fullmatch(r"https://www\.instagram\.com/[\w.]+/", url) is not None


def output_dir(save_dir, site, name):
    # X keeps its original layout so earlier downloads aren't repeated.
    if site == "x":
        return os.path.join(save_dir, name)
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


def start_service(spec):
    from jnius import autoclass  # type: ignore
    import json
    act = autoclass("org.kivy.android.PythonActivity").mActivity
    svc = autoclass("org.personal.xmediadownloader.ServiceDownloader")
    svc.start(act, json.dumps(spec))


def request_notifications():
    if platform == "android" and sdk_int() >= 33:
        from android.permissions import request_permissions  # type: ignore
        request_permissions(["android.permission.POST_NOTIFICATIONS"])


# ---------- Android WebView login helpers ----------
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
    padding: [dp(16), (self.height - self.line_height) / 2]

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

<Root>:
    Image:
        source: "bg.png"
        allow_stretch: True
        keep_ratio: False
        size_hint: 1, 1
        pos: root.pos

    BoxLayout:
        orientation: "vertical"
        padding: [dp(18), dp(30), dp(18), dp(18)]
        spacing: dp(16)

        BoxLayout:
            size_hint_y: None
            height: dp(60)
            spacing: dp(12)
            Image:
                source: "icon.png"
                size_hint: None, None
                size: dp(54), dp(54)
                pos_hint: {"center_y": .5}
            BoxLayout:
                orientation: "vertical"
                Label:
                    text: "Media Downloader"
                    font_size: "22sp"
                    bold: True
                    halign: "left"
                    valign: "bottom"
                    text_size: self.size
                Label:
                    text: "X · TikTok · Snapchat · Instagram"
                    font_size: "13sp"
                    color: 1, 1, 1, .6
                    halign: "left"
                    valign: "top"
                    text_size: self.size

        ScrollView:
            do_scroll_x: False
            bar_width: dp(3)
            bar_color: 1, 1, 1, .3
            BoxLayout:
                orientation: "vertical"
                size_hint_y: None
                height: self.minimum_height
                spacing: dp(16)
                padding: [0, 0, 0, dp(8)]
                GlassCard:
                    Caption:
                        text: "PROFILE OR POST LINK"
                    BoxLayout:
                        size_hint_y: None
                        height: dp(50)
                        spacing: dp(8)
                        GlassInput:
                            id: url
                            hint_text: "Paste a profile or post link"
                        GhostButton:
                            text: "Clear" if url.text else "Paste"
                            size_hint_x: None
                            width: dp(96)
                            on_release: root.paste_or_clear()
                    Caption:
                        text: "X ACCOUNT"
                    BoxLayout:
                        size_hint_y: None
                        height: dp(50)
                        spacing: dp(8)
                        GlassInput:
                            id: token
                            hint_text: "auth_token"
                            password: True
                        GhostButton:
                            id: login_btn
                            text: "Log in to X"
                            size_hint_x: None
                            width: dp(124)
                            on_release: root.login("x")
                    Caption:
                        text: "TIKTOK ACCOUNT  (optional)"
                    BoxLayout:
                        size_hint_y: None
                        height: dp(50)
                        spacing: dp(8)
                        GlassInput:
                            id: tt_state
                            readonly: True
                            hint_text: "Not signed in"
                        GhostButton:
                            id: tt_btn
                            text: "Log in"
                            size_hint_x: None
                            width: dp(124)
                            on_release: root.login("tiktok")
                    Caption:
                        text: "INSTAGRAM ACCOUNT  (required)"
                    BoxLayout:
                        size_hint_y: None
                        height: dp(50)
                        spacing: dp(8)
                        GlassInput:
                            id: ig_state
                            readonly: True
                            hint_text: "Not signed in"
                        GhostButton:
                            id: ig_btn
                            text: "Log in"
                            size_hint_x: None
                            width: dp(124)
                            on_release: root.login("instagram")
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
                        text: "INCLUDE"
                    BoxLayout:
                        size_hint_y: None
                        height: dp(40)
                        spacing: dp(8)
                        Chip:
                            id: images
                            text: "Photos"
                            state: "down"
                        Chip:
                            id: videos
                            text: "Videos"
                            state: "down"
                        Chip:
                            id: retweets
                            text: "X reposts"
                    Widget:
                        size_hint_y: None
                        height: dp(2)
                    PrimaryButton:
                        id: btn
                        text: "Stop" if root.busy else "Download"
                        size_hint_y: None
                        height: dp(56)
                        on_release: root.start()
                GlassCard:
                    size_hint_y: None
                    height: dp(300)
                    spacing: dp(8)
                    BoxLayout:
                        size_hint_y: None
                        height: dp(24)
                        spacing: dp(10)
                        Widget:
                            size_hint: None, None
                            size: dp(10), dp(10)
                            pos_hint: {"center_y": .5}
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
                            valign: "middle"
                            text_size: self.size
                            shorten: True
                            shorten_from: "right"
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
"""


class AccessPopup(ModalView):
    def allow(self):
        self.dismiss()
        request_file_access()


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


ROW_IDS = {"tiktok": ("tt_state", "tt_btn"), "instagram": ("ig_state", "ig_btn")}


class Root(FloatLayout):
    status_color = ListProperty(IDLE)
    busy = BooleanProperty(False)
    progress = NumericProperty(-1)

    def __init__(self, store, **kw):
        super().__init__(**kw)
        self.store = store
        self.running = False
        self.count = 0
        ids = self.ids
        self.url, self.token, self.login_btn = ids.url, ids.token, ids.login_btn
        self.images, self.videos, self.retweets = ids.images, ids.videos, ids.retweets
        self.btn, self.status, self.log, self.scroll = ids.btn, ids.status, ids.log, ids.scroll
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
    def paste_or_clear(self):
        if self.url.text:
            self.url.text = ""
            self.url.focus = True
            return
        try:
            from kivy.core.clipboard import Clipboard
            text = (Clipboard.paste() or "").strip()
        except Exception:
            text = ""
        if text:
            self.url.text = text
            self.url.cursor = (0, 0)
        else:
            self.set_status("Clipboard is empty. Copy a link first.")

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
        Clock.schedule_once(lambda *_: setattr(self.status, "text", text))

    # ---- in-app X login (Android WebView) ----
    LOGIN = {
        "x": ("https://x.com/i/flow/login", "auth_token", "X"),
        "tiktok": ("https://www.tiktok.com/login/phone-or-email/email", "sessionid", "TikTok"),
        "instagram": ("https://www.instagram.com/accounts/login/", "sessionid", "Instagram"),
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
        self.set_status(f"Log in to {name} with username/email and password, then wait or tap Done.")
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
            self.store.put(site, cookies=jar)
            state_id, btn_id = ROW_IDS[site]
            def _ok(*_):
                self.ids[state_id].text = "Signed in"
                self.ids[btn_id].text = "Sign out"
        Clock.schedule_once(_ok)
        self.set_status(f"Logged in to {name}. Paste a link and tap Download.")
        self.set_state(OK)

    def on_back(self):
        if self._login_active:
            self._login_done.set()
            return True
        return False

    # ---- downloading (runs in a background service on Android) ----
    @property
    def status_dir(self):
        return os.path.join(App.get_running_app().user_data_dir, "job")

    def start(self):
        if self.busy:
            engine.request_cancel(self.status_dir)
            self.set_status("Stopping…")
            return
        link = resolve_link(self.url.text)
        if not link:
            self.set_status("Paste an X, TikTok, Snapchat or Instagram link.")
            self.set_state(ERR)
            return
        site, user, gurl = link
        if not (self.images.active or self.videos.active):
            self.set_status("Pick Photos and/or Videos.")
            self.set_state(ERR)
            return
        token = self.token.text.strip()
        if token:
            self.store.put("auth", token=token)
        if site == "instagram" and not self.store.exists("instagram"):
            self.set_status("Log in to Instagram first (Instagram account → Log in).")
            self.set_state(ERR)
            return
        if not self.ensure_access():
            self.set_status("Allow file access, then tap Download again.")
            return
        out = output_dir(self.save_dir, site, user)
        os.makedirs(out, exist_ok=True)
        spec = {
            "args": build_args(site, gurl, out, token, self.images.active,
                               self.videos.active, self.retweets.active,
                               self.store.get(site)["cookies"]
                               if site in ROW_IDS and self.store.exists(site) else None),
            "out": out, "out_label": pretty_path(out), "site": site, "user": user,
            "status_dir": self.status_dir,
        }
        os.makedirs(self.status_dir, exist_ok=True)
        for f in engine.paths(self.status_dir):
            try:
                os.remove(f)
            except OSError:
                pass

        self.log.text = ""
        self._log_pos = 0
        self.progress = -1
        self.busy = True
        self.set_status(f"Downloading {SITE_NAMES[site]} @{user}…")
        self.set_state(BUSY)
        if platform == "android":
            try:
                start_service(spec)
            except Exception as e:
                self.write(f"Background mode unavailable ({e}); downloading in the app.")
                threading.Thread(target=lambda: engine.Job(spec, scan=media_scan).run(),
                                 daemon=True).start()
        else:
            threading.Thread(target=lambda: engine.Job(spec).run(), daemon=True).start()
        self._watch_job()

    def _watch_job(self):
        if self._job_poll is None:
            self._job_poll = Clock.schedule_interval(self._poll_job, 0.5)

    def resume_job(self):
        if self._job_poll is not None:
            return
        st = engine.read_status(self.status_dir)
        if st and st.get("state") == "running" and time.time() - st.get("beat", 0) < 45:
            self.busy = True
            self.set_state(BUSY)
            self._log_pos = 0
            self.log.text = ""
            self._watch_job()

    def _poll_job(self, *_):
        st = engine.read_status(self.status_dir)
        _, log_path, _ = engine.paths(self.status_dir)
        try:
            with open(log_path, encoding="utf-8") as f:
                f.seek(self._log_pos)
                chunk = f.read()
                self._log_pos = f.tell()
            if chunk:
                lines = (self.log.text + chunk).splitlines()[-60:]
                self.log.text = "\n".join(lines) + "\n"
                self.scroll.scroll_y = 0
        except OSError:
            pass
        if not st:
            return
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
            self.busy = False
            if self._job_poll is not None:
                self._job_poll.cancel()
                self._job_poll = None


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
        Window.bind(on_keyboard=self._on_key)
        return self.root_widget

    def on_pause(self):
        return True  # keep running while the folder picker or Settings is open

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
