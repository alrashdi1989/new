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
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
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


# ---------- Android WebView login helpers ----------
_container = None
_webview = None
_click_ref = None

if platform == "android":
    from android.runnable import run_on_ui_thread  # type: ignore
    from jnius import PythonJavaClass, autoclass, java_method  # type: ignore

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

    @run_on_ui_thread
    def webview_open(url, on_done):
        global _container, _webview, _click_ref
        if _container is not None:
            return
        cm = _CookieManager.getInstance()
        cm.setAcceptCookie(True)

        box = _LinearLayout(_activity)
        box.setOrientation(_LinearLayout.VERTICAL)
        box.setBackgroundColor(_Color.WHITE)

        btn = _AButton(_activity)
        btn.setText("Done - back to app")
        _click_ref = _ClickListener(on_done)
        btn.setOnClickListener(_click_ref)
        box.addView(btn, _LLParams(-1, -2))

        wv = _WebView(_activity)
        st = wv.getSettings()
        st.setJavaScriptEnabled(True)
        st.setDomStorageEnabled(True)
        st.setUserAgentString(st.getUserAgentString().replace("; wv", ""))
        cm.setAcceptThirdPartyCookies(wv, True)
        wv.setWebViewClient(_WebViewClient())
        box.addView(wv, _LLParams(-1, 0, 1.0))

        _activity.addContentView(box, _VGParams(-1, -1))
        wv.loadUrl(url)
        _container, _webview = box, wv

    @run_on_ui_thread
    def webview_close():
        global _container, _webview
        if _container is None:
            return
        parent = _container.getParent()
        if parent is not None:
            parent.removeView(_container)
        try:
            _webview.stopLoading()
            _webview.destroy()
        except Exception:
            pass
        _container = _webview = None

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
    def webview_open(url, on_done):
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


class Root(BoxLayout):
    def __init__(self, store, **kw):
        super().__init__(orientation="vertical", padding=dp(14), spacing=dp(8), **kw)
        self.store = store
        self.running = False
        self.count = 0

        self.add_widget(Label(text="[b]X Media Downloader[/b]", markup=True,
                              size_hint_y=None, height=dp(40), font_size="20sp"))
        self.url = TextInput(hint_text="Profile URL  (https://x.com/username)", multiline=False,
                             size_hint_y=None, height=dp(46))
        self.add_widget(self.url)

        saved = store.get("auth")["token"] if store.exists("auth") else ""
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.token = TextInput(text=saved, hint_text="auth_token (or tap Log in)",
                               password=True, multiline=False)
        self.login_btn = Button(text="Logged in ✓" if saved else "Log in to X",
                                size_hint_x=None, width=dp(130))
        self.login_btn.bind(on_release=lambda *_: self.login())
        row.add_widget(self.token)
        row.add_widget(self.login_btn)
        self.add_widget(row)
        self._login_active = False
        self._login_done = threading.Event()

        opts = BoxLayout(size_hint_y=None, height=dp(40))
        self.images, self.videos, self.retweets = CheckBox(active=True), CheckBox(active=True), CheckBox()
        for cb, txt in ((self.images, "Photos"), (self.videos, "Videos"), (self.retweets, "Reposts")):
            opts.add_widget(cb)
            opts.add_widget(Label(text=txt))
        self.add_widget(opts)

        self.btn = Button(text="Download", size_hint_y=None, height=dp(52), font_size="18sp")
        self.btn.bind(on_release=lambda *_: self.start())
        self.add_widget(self.btn)

        self.status = Label(text="Ready.", size_hint_y=None, height=dp(30))
        self.add_widget(self.status)

        self.log = Label(text="", size_hint_y=None, halign="left", valign="top", font_size="12sp")
        self.log.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
                      texture_size=lambda w, v: setattr(w, "height", v[1]))
        sv = ScrollView()
        sv.add_widget(self.log)
        self.scroll = sv
        self.add_widget(sv)

    def write(self, line):
        def _do(*_):
            lines = (self.log.text + line + "\n").splitlines()[-300:]
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
            return
        self._login_active = True
        self._login_done = threading.Event()
        webview_open("https://x.com/i/flow/login", self._login_done.set)
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
        if token:
            self.store.put("auth", token=token)
            def _ok(*_):
                self.token.text = token
                self.login_btn.text = "Logged in ✓"
            Clock.schedule_once(_ok)
            self.set_status("Logged in. Paste a profile URL and tap Download.")
        else:
            names = ", ".join(webview_cookie_names()) or "none"
            self.set_status("Login not detected. Try again.")
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
            return
        if not (self.images.active or self.videos.active):
            self.set_status("Tick Photos and/or Videos.")
            return
        token = self.token.text.strip()
        if token:
            self.store.put("auth", token=token)
        out = os.path.join(download_root(), user)
        os.makedirs(out, exist_ok=True)

        self.log.text = ""
        self.count = 0
        self.running = True
        self.btn.disabled = True
        self.set_status(f"Downloading @{user}…")
        args = build_args(user, out, token, self.images.active, self.videos.active, self.retweets.active)
        threading.Thread(target=self.run, args=(args, out), daemon=True).start()

    def on_line(self, line):
        if line.startswith("#"):
            self.write("skip " + os.path.basename(line[1:].strip()))
        elif not line.startswith("[") and os.sep in line:
            self.count += 1
            self.write("✓ " + os.path.basename(line))
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
        msg = "Finished." if rc == 0 else f"Finished with errors (code {rc})."
        self.set_status(f"{msg} {self.count} new files → {out}")
        def _done(*_):
            self.running = False
            self.btn.disabled = False
        Clock.schedule_once(_done)


class XMediaApp(App):
    title = "X Media Downloader"

    def build(self):
        Window.softinput_mode = "below_target"
        if platform == "android":
            from android.permissions import Permission, request_permissions  # type: ignore
            request_permissions([Permission.WRITE_EXTERNAL_STORAGE, Permission.READ_EXTERNAL_STORAGE])
        self.root_widget = Root(JsonStore(os.path.join(self.user_data_dir, "settings.json")))
        Window.bind(on_keyboard=self._on_key)
        return self.root_widget

    def _on_key(self, window, key, *args):
        if key == 27:  # Android Back
            return self.root_widget.on_back()
        return False


if __name__ == "__main__":
    XMediaApp().run()
