"""
Automatic favorites checks.

The app saves what to check (its favorites as ready-made download jobs) in
auto.json next to its settings. An Android alarm starts the "Autocheck"
background service at the chosen interval; the service downloads anything new
and sets the alarm for the next check, so it keeps going even if the app
isn't opened.
"""

import json
import os
import time

REQUEST_CODE = 4711
SERVICE = "org.personal.xmediadownloader.ServiceAutocheck"


def spec_path(data_dir):
    return os.path.join(data_dir, "auto.json")


def status_dir(data_dir):
    return os.path.join(data_dir, "auto")


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _pending(ctx, path):
    from jnius import autoclass  # type: ignore
    PendingIntent = autoclass("android.app.PendingIntent")
    svc = autoclass(SERVICE)
    intent = svc.getDefaultIntent(ctx, "", "Media Downloader", "Checking favorites",
                                  json.dumps({"auto": True, "spec_path": path}))
    flags = PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
    if autoclass("android.os.Build$VERSION").SDK_INT < 26:      # Android 7
        return PendingIntent.getService(ctx, REQUEST_CODE, intent, flags)
    return PendingIntent.getForegroundService(ctx, REQUEST_CODE, intent, flags)


def schedule(ctx, path, hours, first_in=None):
    """Set the alarm for the next check (replaces any earlier one).
    Returns the time it will run, or None if checks are off."""
    from jnius import autoclass  # type: ignore
    Context = autoclass("android.content.Context")
    AlarmManager = autoclass("android.app.AlarmManager")
    Build = autoclass("android.os.Build$VERSION")
    am = ctx.getSystemService(Context.ALARM_SERVICE)
    pi = _pending(ctx, path)
    am.cancel(pi)
    if not hours:
        return None
    delay = (hours * 3600) if first_in is None else first_in
    when = time.time() + delay
    ms = int(when * 1000)
    exact_ok = Build.SDK_INT < 31 or am.canScheduleExactAlarms()
    if exact_ok:
        am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, ms, pi)
    else:
        am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, ms, pi)
    data = load(path)
    data["next"] = when
    save(path, data)
    return when
