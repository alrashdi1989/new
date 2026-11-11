"""
Floating bubble: a small app icon that stays on top of other apps. Tap it and
it downloads the link you last copied.

How a tap works:
  1. Try to read the copied link right here (works on older Android).
  2. If Android blocks reading the clipboard in the background (common on
     Android 10+), briefly open the app, which can read the clipboard reliably,
     start the download, and drop back to where you were.

Runs as its own foreground service. All window work happens on one dedicated
looper thread (a p4a service's main thread runs Kivy's loop, not an Android
one, so posting there wouldn't run). Only touches the clipboard when you tap.
"""

import json
import os
import sys
import time
import traceback

from jnius import autoclass, cast, PythonJavaClass, java_method

PythonService = autoclass("org.kivy.android.PythonService")
Context = autoclass("android.content.Context")
Intent = autoclass("android.content.Intent")
LayoutParams = autoclass("android.view.WindowManager$LayoutParams")
PixelFormat = autoclass("android.graphics.PixelFormat")
MotionEvent = autoclass("android.view.MotionEvent")
ImageView = autoclass("android.widget.ImageView")
EditText = autoclass("android.widget.EditText")
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


def _excepthook(exc_type, exc, tb):
    log("UNCAUGHT: " + "".join(traceback.format_exception(exc_type, exc, tb)))


sys.excepthook = _excepthook


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
    """Run fn on our own looper thread (where window work must happen)."""
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
    """Try to read the copied link in the background; if that's blocked, let the
    app read it instead (it opens for a moment and drops back)."""
    def add_focus():
        try:
            focus = EditText(SERVICE)
            focus.setFocusableInTouchMode(True)
            lp = LayoutParams(1, 1, _overlay_type(),
                              LayoutParams.FLAG_NOT_TOUCH_MODAL
                              | LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                              PixelFormat.TRANSLUCENT)
            lp.gravity = Gravity.TOP | Gravity.START
            _state["wm"].addView(focus, lp)
            focus.requestFocus()
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
        import links
        found, _ = links.parse_links(text or "")
        if found:
            log("clipboard read ok; downloading")
            _handle_text(text)                 # silent path, no app open
        else:
            log("clipboard blocked; handing off to the app")
            _launch_app_quickpaste()

    ui(add_focus)
    ui(read_and_go, delay=200)


def _launch_app_quickpaste():
    """Open the app briefly so it can read the clipboard and start the download."""
    try:
        pm = SERVICE.getPackageManager()
        intent = pm.getLaunchIntentForPackage(PKG)
        if intent is None:
            toast("Open the app to download this link.")
            return
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP)
        intent.putExtra("quickpaste", True)
        SERVICE.startActivity(intent)
    except Exception as e:
        log("_launch_app_quickpaste: " + repr(e))
        toast("Open the app to download this link.")


def _handle_text(text):
    if _state["busy"]:
        return
    try:
        import quickjob
        spec, msg = quickjob.build_quick(text or "", DATA_DIR)
        toast(msg)
        if not spec:
            log("no link in text (len=%d)" % len(text or ""))
            return
        _state["busy"] = True
        log("starting downloader with %d job(s)" % len(spec["jobs"]))
        autoclass(PKG + ".ServiceDownloader").start(SERVICE, json.dumps(spec))
        log("downloader start() returned")
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
    try:
        if os.path.exists(STOP_FLAG):
            os.remove(STOP_FLAG)
    except OSError:
        pass
    show_bubble()
    started = time.time()
    while not os.path.exists(STOP_FLAG):
        # the heartbeat is only needed right after start (for the app's check);
        # after that just watch for the stop flag, and sleep longer to stay cool.
        if time.time() - started < 60:
            try:
                with open(ALIVE_FLAG, "w") as fh:
                    fh.write(str(time.time()))
            except OSError:
                pass
            time.sleep(2)
        else:
            time.sleep(5)
    hide_bubble()
    for f in (STOP_FLAG, ALIVE_FLAG):
        try:
            os.remove(f)
        except OSError:
            pass
    log("service stop")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("main crashed:\n" + traceback.format_exc())
