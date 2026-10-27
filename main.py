"""
X Media Downloader — Android (Kivy) version.

Paste an X profile URL, paste your x.com `auth_token` cookie once,
tap Download. Files go to  Download/X-media/<username>/  on the phone.
"""

import io
import os
import re
import sys
import threading

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.storage.jsonstore import JsonStore
from kivy.lang import Builder
from kivy.properties import ListProperty
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.modalview import ModalView
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton
from kivy.utils import platform

RESERVED = {"home", "explore", "search", "notifications", "messages", "i", "settings",
            "compose", "login", "logout", "signup", "tos", "privacy", "hashtag", "intent"}
IMAGE_EXT = ("jpg", "jpeg", "png", "webp")
VIDEO_EXT = ("mp4", "mov", "m4v", "webm")


def parse_username(text):
    text = text.strip()
    m = re.match(r"^(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/([^/?#]+)", text, re.I)
    name = m.group(1) if m else text.lstrip("@")
    if re.fullmatch(r"[A-Za-z0-9_]{1,15}", name) and name.lower() not in RESERVED:
        return name
    return None


def build_args(user, out_dir, auth_token, images, videos, retweets):
    exts = (IMAGE_EXT if images else ()) + (VIDEO_EXT if videos else ())
    args = [
        "-d", out_dir,
        "--download-archive", os.path.join(out_dir, ".downloaded.sqlite3"),
        "-o", f"retweets={'true' if retweets else 'false'}",
        "-o", "videos=true",
        "-o", "directory=[]",
        "--filter", f"extension in {exts!r}",
    ]
    if auth_token:
        args += ["-o", 'cookies={"auth_token": "%s"}' % auth_token.strip()]
    args.append(f"https://x.com/{user}/media")
    return args


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

    def _cookie_string():
        cm = _CookieManager.getInstance()
        parts = []
        for u in ("https://x.com", "https://twitter.com", "https://www.x.com"):
            c = cm.getCookie(u)
            if c:
                parts.append(c)
        return "; ".join(parts)

    def webview_auth_token():
        for part in _cookie_string().split(";"):
            k, _, v = part.strip().partition("=")
            if k == "auth_token" and v:
                return v
        return None

    def webview_cookie_names():
        names = []
        for part in _cookie_string().split(";"):
            k = part.strip().partition("=")[0]
            if k and k not in names:
                names.append(k)
        return names
else:
    def webview_open(url, on_done, on_error):
        pass

    def webview_close():
        pass

    def webview_auth_token():
        return None

    def webview_cookie_names():
        return []


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
                    text: "Save photos and videos from X profiles"
                    font_size: "13sp"
                    color: 1, 1, 1, .6
                    halign: "left"
                    valign: "top"
                    text_size: self.size

        GlassCard:
            Caption:
                text: "PROFILE"
            GlassInput:
                id: url
                hint_text: "https://x.com/username"
            Caption:
                text: "ACCOUNT"
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
                    on_release: root.login()
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
                    text: "Reposts"
            Widget:
                size_hint_y: None
                height: dp(2)
            PrimaryButton:
                id: btn
                text: "Downloading..." if self.disabled else "Download"
                size_hint_y: None
                height: dp(56)
                on_release: root.start()

        GlassCard:
            size_hint_y: 1
            height: 0
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


class Root(FloatLayout):
    status_color = ListProperty(IDLE)

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
        self._new_files = []
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
    def login(self):
        if platform != "android":
            self.set_status("In-app login works on Android only. Paste the token instead.")
            return
        if self._login_active:
            self._login_done.set()
            self.set_status("Login reset. Tap Log in to X again.")
            return
        self._login_active = True
        self._login_done = threading.Event()
        webview_open("https://x.com/i/flow/login", self._login_done.set, self.write)
        self.set_status("Log in to X, then wait or tap Done.")
        threading.Thread(target=self._watch_login, daemon=True).start()

    def _watch_login(self):
        # Runs off the UI threads so it keeps working while the login page is on top.
        import time
        token, deadline = None, time.time() + 900
        while time.time() < deadline:
            try:
                token = webview_auth_token()
            except Exception as e:
                self.write(f"Login check error: {e}")
            if token or self._login_done.is_set():
                break
            time.sleep(1)
        if not token:
            try:
                token = webview_auth_token()
            except Exception:
                pass
        webview_close()
        self._login_active = False
        if platform == "android":
            def _report(*_):
                while _close_error:
                    self.write("Close issue: " + _close_error.pop(0))
            Clock.schedule_once(_report, 1.5)
        if token:
            self.store.put("auth", token=token)
            def _ok(*_):
                self.token.text = token
                self.login_btn.text = "Signed in"
            Clock.schedule_once(_ok)
            self.set_status("Logged in. Paste a profile URL and tap Download.")
            self.set_state(OK)
        else:
            names = ", ".join(webview_cookie_names()) or "none"
            self.set_status("Login not detected. Try again.")
            self.set_state(ERR)
            self.write(f"No auth_token found. Cookies seen: {names}")

    def on_back(self):
        if self._login_active:
            self._login_done.set()
            return True
        return False

    def start(self):
        if self.running:
            return
        user = parse_username(self.url.text)
        if not user:
            self.set_status("Invalid profile URL.")
            self.set_state(ERR)
            return
        if not (self.images.active or self.videos.active):
            self.set_status("Pick Photos and/or Videos.")
            self.set_state(ERR)
            return
        token = self.token.text.strip()
        if token:
            self.store.put("auth", token=token)
        if not self.ensure_access():
            self.set_status("Allow file access, then tap Download again.")
            return
        out = os.path.join(self.save_dir, user)
        os.makedirs(out, exist_ok=True)

        self.log.text = ""
        self.count = 0
        self._new_files = []
        self.running = True
        self.btn.disabled = True
        self.set_status(f"Downloading @{user}…")
        self.set_state(BUSY)
        args = build_args(user, out, token, self.images.active, self.videos.active, self.retweets.active)
        threading.Thread(target=self.run, args=(args, out), daemon=True).start()

    def _flush_scan(self):
        batch, self._new_files = self._new_files, []
        try:
            media_scan(batch)
        except Exception as e:
            self.write(f"Gallery scan issue: {e}")

    def on_line(self, line):
        if line.startswith("#"):
            self.write("skip " + os.path.basename(line[1:].strip()))
        elif not line.startswith("[") and os.sep in line:
            self.count += 1
            self._new_files.append(line.strip())
            if len(self._new_files) >= 25:
                self._flush_scan()
            self.write("+ " + os.path.basename(line))
            self.set_status(f"{self.count} files downloaded…")
        else:
            self.write(line)
            low = line.lower()
            if "401" in low or "login" in low or "authenticat" in low:
                self.write("→ Paste a valid auth_token cookie (see README).")

    def run(self, args, out):
        import logging
        import gallery_dl
        writer = LineWriter(self.on_line)
        old_out, old_err, old_argv = sys.stdout, sys.stderr, sys.argv
        logging.getLogger().handlers.clear()
        gallery_dl.config.clear()
        sys.stdout = sys.stderr = writer
        sys.argv = ["gallery-dl"] + args
        rc = 1
        try:
            self.write("Fetching media list…")
            rc = gallery_dl.main() or 0
        except SystemExit as e:
            rc = e.code or 0
        except Exception as e:
            self.write(f"Error: {e}")
        finally:
            sys.stdout, sys.stderr, sys.argv = old_out, old_err, old_argv
        self._flush_scan()
        try:
            media_scan_folder(out)  # also catches files from older versions
        except Exception as e:
            self.write(f"Gallery scan issue: {e}")
        msg = "Finished." if rc == 0 else f"Finished with errors (code {rc})."
        self.set_state(OK if rc == 0 else ERR)
        self.set_status(f"{msg} {self.count} new files")
        self.write(f"Saved in: {pretty_path(out)}")
        def _done(*_):
            self.running = False
            self.btn.disabled = False
        Clock.schedule_once(_done)


class XMediaApp(App):
    title = "X Media Downloader"

    def build(self):
        Window.softinput_mode = "below_target"
        Window.clearcolor = (9/255, 12/255, 28/255, 1)
        self.root_widget = Root(JsonStore(os.path.join(self.user_data_dir, "settings.json")))
        if platform == "android":
            Clock.schedule_once(lambda *_: self.root_widget.ensure_access(), 1)
        Window.bind(on_keyboard=self._on_key)
        return self.root_widget

    def on_pause(self):
        return True  # keep running while the folder picker or Settings is open

    def on_resume(self):
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
