"""
Floating bubble: a small app icon that stays on top of other apps. Tap it and
it grabs the link you last copied and starts downloading, without opening the
app. Drag it to move it.

Runs as its own foreground service (a separate process), so it talks to Android
directly through pyjnius and posts all window work to this process's own main
thread — it does NOT use the app's Activity, which isn't available here. It only
reads the clipboard at the moment you tap the bubble, never in the background.
"""

import json
import os
import time
import traceback

from jnius import autoclass, cast, PythonJavaClass, java_method

PythonService = autoclass("org.kivy.android.PythonService")
Context = autoclass("android.content.Context")
LayoutParams = autoclass("android.view.WindowManager$LayoutParams")
PixelFormat = autoclass("android.graphics.PixelFormat")
MotionEvent = autoclass("android.view.MotionEvent")
ImageView = autoclass("android.widget.ImageView")
Toast = autoclass("android.widget.Toast")
BuildVersion = autoclass("android.os.Build$VERSION")
Gravity = autoclass("android.view.Gravity")
Handler = autoclass("android.os.Handler")
Looper = autoclass("android.os.Looper")
HandlerThread = autoclass("android.os.HandlerThread")
String = autoclass("java.lang.String")
CharSequence = autoclass("java.lang.CharSequence")

SERVICE = PythonService.mService
PKG = SERVICE.getPackageName()
DATA_DIR = SERVICE.getFilesDir().getAbsolutePath()
STOP_FLAG = os.path.join(DATA_DIR, "float_stop")
ALIVE_FLAG = os.path.join(DATA_DIR, "float_alive")
LOG = os.path.join(DATA_DIR, "float.log")

_state = {"view": None, "params": None, "focus": None, "wm": None,
          "busy": False, "touch": None}
_hold = []                      # keep Java callback objects alive until they run
_handler = None


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + str(msg) + "\n")
    except OSError:
        pass


def dp(px):
    try:
        return int(px * SERVICE.getResources().getDisplayMetrics().density)
    except Exception:
        return int(px * 2.75)


class _Runnable(PythonJavaClass):
    __javainterfaces__ = ["java/lang/Runnable"]

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    @java_method("()V")
    def run(self):
        try:
            self.fn()
        except Exception as e:
            log("ui run: " + repr(e) + "\n" + traceback.format_exc())
        finally:
            try:
                _hold.remove(self)
            except ValueError:
                pass


def ui(fn, delay=0):
    """Run fn on our own looper thread. A dedicated HandlerThread (rather than
    the main one) is used because in p4a the main thread runs Kivy's loop, not
    an Android message loop, so posts to it may never run. All window work
    happens on this one thread, which is what WindowManager requires."""
    global _handler
    if _handler is None:
        ht = HandlerThread("floatie-ui")
        ht.start()
        _handler = Handler(ht.getLooper())
    r = _Runnable(fn)
    _hold.append(r)
    if delay:
        _handler.postDelayed(r, delay)
    else:
        _handler.post(r)


def toast(msg):
    def show():
        try:
            Toast.makeText(SERVICE, cast(CharSequence, String(msg)), Toast.LENGTH_LONG).show()
        except Exception as e:
            log("toast: " + repr(e))
    ui(show)


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
    cm = cast("android.content.ClipboardManager",
              SERVICE.getSystemService(Context.CLIPBOARD_SERVICE))
    try:
        if not cm.hasPrimaryClip():
            return ""
        clip = cm.getPrimaryClip()
        if clip is None or clip.getItemCount() == 0:
            return ""
        text = clip.getItemAt(0).coerceToText(SERVICE)
        return text.toString() if text else ""
    except Exception as e:
        log("clipboard: " + repr(e))
        return ""


def on_tap():
    """Add a tiny focusable window (so Android lets us read the clipboard),
    read it after a moment, remove the window, then start the download."""
    def add_focus():
        try:
            focus = ImageView(SERVICE)
            lp = LayoutParams(1, 1, _overlay_type(),
                              LayoutParams.FLAG_NOT_TOUCH_MODAL
                              | LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                              PixelFormat.TRANSLUCENT)
            lp.gravity = Gravity.TOP | Gravity.START
            _state["wm"].addView(focus, lp)
            _state["focus"] = focus
        except Exception as e:
            log("add_focus: " + repr(e))

    def read_and_go():
        try:
            text = _read_clipboard()
        finally:
            try:
                if _state.get("focus") is not None:
                    _state["wm"].removeView(_state["focus"])
                    _state["focus"] = None
            except Exception as e:
                log("remove_focus: " + repr(e))
        _handle_text(text)

    ui(add_focus)
    ui(read_and_go, delay=150)


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
        autoclass(PKG + ".ServiceDownloader").start(SERVICE, json.dumps(spec))
    except Exception as e:
        log("_handle_text: " + repr(e) + "\n" + traceback.format_exc())
        toast("Couldn't start the download. Open the app and try there.")
    finally:
        import threading
        threading.Timer(2.0, lambda: _state.update(busy=False)).start()


def show_bubble():
    def add():
        try:
            wm = cast("android.view.WindowManager",
                      SERVICE.getSystemService(Context.WINDOW_SERVICE))
            _state["wm"] = wm
            view = ImageView(SERVICE)
            try:
                view.setImageDrawable(
                    SERVICE.getPackageManager().getApplicationIcon(PKG))
            except Exception as e:
                log("icon: " + repr(e))
            view.setAlpha(0.92)
            size = dp(56)
            x, y = _load_position()
            params = LayoutParams(size, size, _overlay_type(),
                                  LayoutParams.FLAG_NOT_FOCUSABLE
                                  | LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                                  PixelFormat.TRANSLUCENT)
            params.gravity = Gravity.TOP | Gravity.START
            params.x, params.y = x, y
            listener = TouchListener()
            _state["touch"] = listener            # keep a ref (Java side holds it)
            view.setOnTouchListener(listener)
            wm.addView(view, params)
            _state["view"] = view
            _state["params"] = params
            log("bubble shown at %d,%d" % (x, y))
        except Exception as e:
            log("show_bubble: " + repr(e) + "\n" + traceback.format_exc())
            toast("Floating button couldn't be shown: " + str(e))
    ui(add)


def hide_bubble():
    def rm():
        try:
            if _state.get("view") is not None:
                _state["wm"].removeView(_state["view"])
                _state["view"] = None
        except Exception as e:
            log("hide_bubble: " + repr(e))
    ui(rm)


def main():
    log("service start (sdk %s)" % BuildVersion.SDK_INT)
    for f in (STOP_FLAG,):
        try:
            if os.path.exists(f):
                os.remove(f)
        except OSError:
            pass
    show_bubble()
    while not os.path.exists(STOP_FLAG):
        try:
            with open(ALIVE_FLAG, "w") as fh:
                fh.write(str(time.time()))
        except OSError:
            pass
        time.sleep(2)
    hide_bubble()
    for f in (STOP_FLAG, ALIVE_FLAG):
        try:
            os.remove(f)
        except OSError:
            pass
    log("service stop")


if __name__ == "__main__":
    main()
