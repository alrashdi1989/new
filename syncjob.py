"""
Once-a-day automatic cloud sync.

An Android alarm starts the background service roughly once every 24 hours;
the service refreshes the login token, pulls the cloud copy, merges it with
what's on this device, pushes the result back, then sets the next alarm. This
keeps favorites (and, if the user turned it on, app logins) in step across
devices without anyone tapping "Sync now". No Kivy here, so both the app and
the service can use it.
"""

import json
import os
import time

REQUEST_CODE = 4712                       # distinct from autocheck's 4711
SERVICE = "org.personal.xmediadownloader.ServiceAutocheck"
PERIOD = 24 * 3600                        # once a day

# Must match main.py.
SYNC_KEYS = ("favorites", "fav_groups", "content", "include", "names", "parallel")
LOGIN_KEYS = ("auth", "instagram", "tiktok", "facebook", "threads")


def store_path(data_dir):
    return os.path.join(data_dir, "settings.json")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _logins_on(store):
    try:
        return bool(store.get("synclogins", {}).get("on"))
    except Exception:
        return False


def collect(store):
    """Build the payload to upload, from a plain settings dict."""
    data = {k: store[k] for k in SYNC_KEYS if k in store}
    if _logins_on(store):
        logins = {k: store[k] for k in LOGIN_KEYS if k in store}
        if logins:
            data["_logins"] = logins
    return data


def apply(store, data, merge_favorites=True):
    """Merge a pulled cloud payload into the settings dict (in place)."""
    if "favorites" in data:
        cloud_favs = data["favorites"].get("items", []) or []
        if merge_favorites and "favorites" in store:
            local = store["favorites"].get("items", []) or []
            seen = {f.get("link") for f in cloud_favs}
            cloud_favs = cloud_favs + [f for f in local if f.get("link") not in seen]
        store["favorites"] = {"items": cloud_favs}
    for k in SYNC_KEYS:
        if k != "favorites" and k in data:
            store[k] = data[k]
    if _logins_on(store) and isinstance(data.get("_logins"), dict):
        for k, v in data["_logins"].items():
            if k in LOGIN_KEYS and isinstance(v, dict):
                store[k] = v
    return store


def run_once(data_dir):
    """Refresh token, pull, merge, push. Safe to call anytime; no-op if not
    logged in. Returns True if a sync happened."""
    import cloudsync
    path = store_path(data_dir)
    store = _read(path)
    cloud = store.get("cloud") or {}
    refresh_token = cloud.get("refreshToken")
    uid = cloud.get("uid")
    if not refresh_token or not uid:
        return False
    try:
        auth = cloudsync.refresh(refresh_token)        # 24h old token is expired
    except Exception:
        return False
    cloud.update(idToken=auth["idToken"], refreshToken=auth["refreshToken"],
                 uid=auth["uid"], expiry=auth["expiry"])
    store["cloud"] = cloud
    idt, uid = auth["idToken"], auth["uid"]
    try:
        remote = cloudsync.pull(idt, uid)
    except Exception:
        remote = None
    if remote and remote.get("data"):
        apply(store, remote["data"], merge_favorites=True)
    try:
        cloudsync.push(idt, uid, collect(store), int(time.time() * 1000))
    except Exception:
        pass
    store["_lastsync"] = {"time": time.time()}
    _write(path, store)
    return True


# ---------- Android alarm (reuses the Autocheck service) ----------
def _pending(ctx, data_dir):
    from jnius import autoclass  # type: ignore
    PendingIntent = autoclass("android.app.PendingIntent")
    svc = autoclass(SERVICE)
    intent = svc.getDefaultIntent(ctx, "", "Media Downloader", "Syncing your devices",
                                  json.dumps({"synctask": True, "data_dir": data_dir}))
    flags = PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
    if autoclass("android.os.Build$VERSION").SDK_INT < 26:
        return PendingIntent.getService(ctx, REQUEST_CODE, intent, flags)
    return PendingIntent.getForegroundService(ctx, REQUEST_CODE, intent, flags)


def schedule(ctx, data_dir, first_in=None):
    """Set (or reset) the daily-sync alarm. first_in overrides the 24h delay."""
    from jnius import autoclass  # type: ignore
    Context = autoclass("android.content.Context")
    AlarmManager = autoclass("android.app.AlarmManager")
    am = ctx.getSystemService(Context.ALARM_SERVICE)
    pi = _pending(ctx, data_dir)
    am.cancel(pi)
    delay = PERIOD if first_in is None else first_in
    ms = int((time.time() + delay) * 1000)
    # Inexact + allow-while-idle: battery friendly, needs no exact-alarm permission.
    am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, ms, pi)
    return time.time() + delay


def cancel(ctx, data_dir):
    from jnius import autoclass  # type: ignore
    Context = autoclass("android.content.Context")
    am = ctx.getSystemService(Context.ALARM_SERVICE)
    am.cancel(_pending(ctx, data_dir))
