"""
Floating bubble: a small app icon that stays on top of other apps. Tap it and
it grabs the link you last copied and starts downloading, without opening the
app. Drag it to move it; drag it onto the "Remove" target to hide it.

This runs as its own foreground service (so Android keeps it alive) and talks
to Android directly through pyjnius, since Kivy has no overlay widget. It only
reads the clipboard at the moment you tap the bubble, never in the background.
"""

import json
import os
import time
import traceback

from jnius import autoclass, cast, PythonJavaClass, java_method
from android.runnable import run_on_ui_thread  # type: ignore

PythonService = autoclass("org.kivy.android.PythonService")
Context = autoclass("android.content.Context")
WindowManager = autoclass("android.view.WindowManager")
LayoutParams = autoclass("android.view.WindowManager$LayoutParams")
PixelFormat = autoclass("android.graphics.PixelFormat")
MotionEvent = autoclass("android.view.MotionEvent")
ImageView = autoclass("android.widget.ImageView")
Toast = autoclass("android.widget.Toast")
BuildVersion = autoclass("android.os.Build$VERSION")
Gravity = autoclass("android.view.Gravity")
String = autoclass("java.lang.String")
CharSequence = autoclass("java.lang.CharSequence")

SERVICE = PythonService.mService
PKG = SERVICE.getPackageName()
DATA_DIR = SERVICE.getFilesDir().getAbsolutePath()
STOP_FLAG = os.path.join(DATA_DIR, "float_stop")
LOG = os.path.join(DATA_DIR, "float.log")

_state = {"view": None, "params": None, "focus": None, "wm": None, "busy": False}


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + str(msg) + "\n")
    except OSError:
        pass


def dp(px):
    try:
        d = SERVICE.getResources().getDisplayMetrics().density
        return int(px * d)
    except Exception:
        return int(px * 2.75)


@run_on_ui_thread
def toast(msg):
    try:
        Toast.makeText(SERVICE, cast(CharSequence, String(msg)), Toast.LENGTH_LONG).show()
    except Exception as e:
        log(f"toast failed: {e}")


def _overlay_type():
    if BuildVersion.SDK_INT >= 26:
        return LayoutParams.TYPE_APPLICATION_OVERLAY
    return 2002          # TYPE_PHONE on older Android


class TouchListener(PythonJavaClass):
    __javainterfaces__ = ["android/view/View$OnTouchListener"]

    def __init__(self):
        super().__init__()
        self.ix = self.iy = 0
        self.tx = self.ty = 0.0
        self.moved = False

    @java_method("(Landroid/view/View;Landroid/view/MotionEvent;)Z")
    def onTouch(self, view, event):
        try:
            action = event.getAction()
            params = _state["params"]
            if action == MotionEvent.ACTION_DOWN:
                self.ix, self.iy = params.x, params.y
                self.tx, self.ty = event.getRawX(), event.getRawY()
                self.moved = False
                return True
            if action == MotionEvent.ACTION_MOVE:
                dx = int(event.getRawX() - self.tx)
                dy = int(event.getRawY() - self.ty)
                if abs(dx) > dp(6) or abs(dy) > dp(6):
                    self.moved = True
                params.x = self.ix + dx
                params.y = self.iy + dy
                _state["wm"].updateViewLayout(view, params)
                return True
            if action == MotionEvent.ACTION_UP:
                _save_position(params.x, params.y)
                if not self.moved:
                    on_tap()
                return True
        except Exception as e:
            log("onTouch: " + repr(e))
        return False


def _save_position(x, y):
    try:
        with open(os.path.join(DATA_DIR, "float_pos.json"), "w") as f:
            json.dump({"x": x, "y": y}, f)
    except OSError:
        pass


def _load_position():
    try:
        with open(os.path.join(DATA_DIR, "float_pos.json")) as f:
            p = json.load(f)
            return int(p.get("x", 0)), int(p.get("y", dp(120)))
    except Exception:
        return 0, dp(120)


def _read_clipboard():
    """Read the copied text. A focusable overlay is shown for a moment so
    Android lets us read the clipboard (newer versions block it otherwise)."""
    cm = cast("android.content.ClipboardManager",
              SERVICE.getSystemService(Context.CLIPBOARD_SERVICE))
    try:
        if not cm.hasPrimaryClip():
            return ""
        clip = cm.getPrimaryClip()
        if clip is None or clip.getItemCount() == 0:
            return ""
        item = clip.getItemAt(0)
        text = item.coerceToText(SERVICE)
        return text.toString() if text else ""
    except Exception as e:
        log("clipboard read: " + repr(e))
        return ""


@run_on_ui_thread
def _add_focus_then_read():
    """Add a 1px focusable window to gain focus, read clipboard, remove it."""
    try:
        wm = _state["wm"]
        focus = ImageView(SERVICE)
        lp = LayoutParams(
            1, 1, _overlay_type(),
            LayoutParams.FLAG_NOT_TOUCH_MODAL | LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT)
        lp.gravity = Gravity.TOP | Gravity.START
        wm.addView(focus, lp)
        _state["focus"] = focus

        Handler = autoclass("android.os.Handler")
        Looper = autoclass("android.os.Looper")

        class Reader(PythonJavaClass):
            __javainterfaces__ = ["java/lang/Runnable"]

            @java_method("()V")
            def run(self):
                try:
                    text = _read_clipboard()
                finally:
                    _remove_focus()
                _handle_text(text)

        Handler(Looper.getMainLooper()).postDelayed(Reader(), 120)
    except Exception as e:
        log("_add_focus_then_read: " + repr(e))
        _remove_focus()
        _handle_text(_read_clipboard())


@run_on_ui_thread
def _remove_focus():
    try:
        if _state.get("focus") is not None:
            _state["wm"].removeView(_state["focus"])
            _state["focus"] = None
    except Exception as e:
        log("_remove_focus: " + repr(e))


def _handle_text(text):
    if _state["busy"]:
        return
    try:
        import quickjob
        spec, msg = quickjob.build_quick(text or "", DATA_DIR)
        toast(msg)
        if not spec:
            return
        _state["busy"] = True
        svc = autoclass(PKG + ".ServiceDownloader")
        svc.start(SERVICE, json.dumps(spec))
    except Exception as e:
        log("_handle_text: " + repr(e) + "\n" + traceback.format_exc())
        toast("Couldn't start the download. Open the app and try there.")
    finally:
        import threading
        threading.Timer(2.0, lambda: _state.update(busy=False)).start()


def on_tap():
    _add_focus_then_read()


@run_on_ui_thread
def _show_bubble():
    try:
        wm = cast("android.view.WindowManager",
                  SERVICE.getSystemService(Context.WINDOW_SERVICE))
        _state["wm"] = wm
        view = ImageView(SERVICE)
        try:
            icon = SERVICE.getPackageManager().getApplicationIcon(PKG)
            view.setImageDrawable(icon)
        except Exception:
            pass
        view.setAlpha(0.92)
        size = dp(56)
        x, y = _load_position()
        params = LayoutParams(
            size, size, _overlay_type(),
            LayoutParams.FLAG_NOT_FOCUSABLE | LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT)
        params.gravity = Gravity.TOP | Gravity.START
        params.x, params.y = x, y
        view.setOnTouchListener(TouchListener())
        wm.addView(view, params)
        _state["view"] = view
        _state["params"] = params
        log("bubble shown")
    except Exception as e:
        log("_show_bubble: " + repr(e) + "\n" + traceback.format_exc())


@run_on_ui_thread
def _hide_bubble():
    try:
        if _state.get("view") is not None:
            _state["wm"].removeView(_state["view"])
            _state["view"] = None
    except Exception as e:
        log("_hide_bubble: " + repr(e))


def main():
    try:
        if os.path.exists(STOP_FLAG):
            os.remove(STOP_FLAG)
    except OSError:
        pass
    _show_bubble()
    # keep the service alive until the app asks it to stop
    while not os.path.exists(STOP_FLAG):
        time.sleep(1)
    _hide_bubble()
    try:
        os.remove(STOP_FLAG)
    except OSError:
        pass
    log("stopped")


if __name__ == "__main__":
    main()
