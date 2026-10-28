"""
Android background service: runs one download job while the app is closed or
the screen is off. Shows progress in a notification.
"""

import json
import os
import threading
import time

from jnius import autoclass, cast

import engine

PythonService = autoclass("org.kivy.android.PythonService")
service = PythonService.mService
service.setAutoRestartService(False)

spec = json.loads(os.environ.get("PYTHON_SERVICE_ARGUMENT") or "{}")

Context = autoclass("android.content.Context")
String = autoclass("java.lang.String")


def cs(text):
    return cast("java.lang.CharSequence", String(text))


# ---- keep the CPU awake while downloading ----
wake = None
try:
    PowerManager = autoclass("android.os.PowerManager")
    pm = service.getSystemService(Context.POWER_SERVICE)
    wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "mediadownloader:download")
    wake.acquire()
except Exception:
    wake = None


# ---- notification ----
FG_ID, DONE_ID, CHANNEL = 1, 2, "downloads"
notify_ok = True
try:
    NotificationManager = autoclass("android.app.NotificationManager")
    NotificationChannel = autoclass("android.app.NotificationChannel")
    Builder = autoclass("android.app.Notification$Builder")
    Intent = autoclass("android.content.Intent")
    PendingIntent = autoclass("android.app.PendingIntent")
    nm = service.getSystemService(Context.NOTIFICATION_SERVICE)
    nm.createNotificationChannel(
        NotificationChannel(CHANNEL, cs("Downloads"), NotificationManager.IMPORTANCE_LOW))
    # Android needs a plain white silhouette here; the colourful app icon shows as a square.
    _sysdraw = autoclass("android.R$drawable")
    icon = _sysdraw.stat_sys_download
    icon_done = _sysdraw.stat_sys_download_done
    launch = service.getPackageManager().getLaunchIntentForPackage(service.getPackageName())
    tap = PendingIntent.getActivity(service, 0, launch, PendingIntent.FLAG_IMMUTABLE)
except Exception:
    notify_ok = False


def notify(text, done=False):
    if not notify_ok:
        return
    try:
        b = Builder(service, CHANNEL)
        b.setContentTitle(cs("Media Downloader"))
        b.setContentText(cs(text))
        b.setSmallIcon(icon_done if done else icon)
        b.setContentIntent(tap)
        if done:
            b.setAutoCancel(True)
            nm.notify(DONE_ID, b.build())
        else:
            b.setOngoing(True)
            service.startForeground(FG_ID, b.build())
    except Exception:
        pass


# ---- media scanner (Gallery / Google Photos) ----
MediaScanner = autoclass("android.media.MediaScannerConnection")


def scan(paths):
    paths = list(paths)
    for i in range(0, len(paths), 200):
        MediaScanner.scanFile(service, paths[i:i + 200], None, None)


# ---- hard stop if a cancel request isn't picked up quickly ----
def cancel_watch():
    cancel_file = engine.paths(spec["status_dir"])[2]
    while True:
        time.sleep(1)
        if os.path.exists(cancel_file):
            time.sleep(6)  # give the engine a chance to stop cleanly
            st = engine.read_status(spec["status_dir"]) or {}
            if st.get("state") == "running":
                st.update(state="cancelled", status="Stopped.", beat=time.time())
                with open(engine.paths(spec["status_dir"])[0], "w") as f:
                    json.dump(st, f)
            finish()
            os._exit(0)


def finish():
    try:
        if wake is not None and wake.isHeld():
            wake.release()
    except Exception:
        pass
    try:
        service.stopForeground(True)
        service.stopSelf()
    except Exception:
        pass


if spec.get("args") or spec.get("jobs"):
    threading.Thread(target=cancel_watch, daemon=True).start()
    try:
        engine.run_queue(spec, scan=scan, notify=notify)
    finally:
        finish()
else:
    finish()
