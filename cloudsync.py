"""
Cloud sync for favorites and preferences (Firebase, the user's own project).

Login is email + password through Firebase Authentication; the synced data is
stored in Firestore under users/<uid>, as one JSON blob, so only the signed-in
user can read or write their own document (enforced by the security rule).

No Kivy here, so it can be reused. Only this user's favorites and preferences
are synced — never logins, cookies or local folder paths.
"""

import json
import time

import requests

API_KEY = "AIzaSyDglLSKXJe5iqJ2VKmVhYSJl0HOvtRb-MU"
PROJECT_ID = "media-downloader"

_IDTK = "https://identitytoolkit.googleapis.com/v1/accounts:"
_TOKEN = "https://securetoken.googleapis.com/v1/token"
_FS = (f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}"
       "/databases/(default)/documents")

TIMEOUT = 30


class SyncError(Exception):
    pass


_MESSAGES = {
    "EMAIL_EXISTS": "That email already has an account — try Log in instead.",
    "INVALID_LOGIN_CREDENTIALS": "Wrong email or password.",
    "INVALID_PASSWORD": "Wrong password.",
    "EMAIL_NOT_FOUND": "No account with that email — try Sign up.",
    "MISSING_PASSWORD": "Enter a password.",
    "MISSING_EMAIL": "Enter your email.",
    "INVALID_EMAIL": "That email doesn't look right.",
    "WEAK_PASSWORD : Password should be at least 6 characters.":
        "Password must be at least 6 characters.",
    "USER_DISABLED": "This account has been disabled.",
    "TOO_MANY_ATTEMPTS_TRY_LATER : Access to this account has been temporarily "
    "disabled due to many failed login attempts. You can immediately restore it "
    "by resetting your password or you can try again later.":
        "Too many tries. Wait a bit, then try again.",
}


def _friendly(msg):
    if msg in _MESSAGES:
        return _MESSAGES[msg]
    if msg.startswith("WEAK_PASSWORD"):
        return "Password must be at least 6 characters."
    if msg.startswith("TOO_MANY_ATTEMPTS"):
        return "Too many tries. Wait a bit, then try again."
    return msg.replace("_", " ").capitalize() if msg else "Something went wrong."


def _auth(endpoint, email, password):
    try:
        r = requests.post(_IDTK + endpoint + "?key=" + API_KEY,
                          json={"email": email.strip(), "password": password,
                                "returnSecureToken": True}, timeout=TIMEOUT)
        d = r.json()
    except requests.RequestException:
        raise SyncError("Couldn't reach the server. Check your internet.")
    if "error" in d:
        raise SyncError(_friendly(d["error"].get("message", "")))
    return {"idToken": d["idToken"], "refreshToken": d["refreshToken"],
            "uid": d["localId"], "email": d.get("email", email.strip()),
            "expiry": time.time() + int(d.get("expiresIn", 3600)) - 120}


def sign_up(email, password):
    return _auth("signUp", email, password)


def sign_in(email, password):
    return _auth("signInWithPassword", email, password)


def refresh(refresh_token):
    try:
        r = requests.post(_TOKEN + "?key=" + API_KEY,
                          data={"grant_type": "refresh_token",
                                "refresh_token": refresh_token}, timeout=TIMEOUT)
        d = r.json()
    except requests.RequestException:
        raise SyncError("Couldn't reach the server. Check your internet.")
    if "error" in d:
        raise SyncError("Please log in again.")
    return {"idToken": d["id_token"], "refreshToken": d["refresh_token"],
            "uid": d["user_id"], "expiry": time.time() + int(d.get("expires_in", 3600)) - 120}


def pull(id_token, uid):
    """Return {"data": {...}, "updated": <ms>} or None if nothing is stored yet."""
    try:
        r = requests.get(f"{_FS}/users/{uid}",
                         headers={"Authorization": "Bearer " + id_token}, timeout=TIMEOUT)
    except requests.RequestException:
        raise SyncError("Couldn't reach the sync server.")
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise SyncError("Couldn't read your synced data.")
    fields = (r.json() or {}).get("fields", {})
    blob = fields.get("data", {}).get("stringValue")
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except ValueError:
        return None
    updated = int(fields.get("updated", {}).get("integerValue", 0) or 0)
    return {"data": data, "updated": updated}


def push(id_token, uid, data, updated_ms):
    body = {"fields": {
        "data": {"stringValue": json.dumps(data)},
        "updated": {"integerValue": str(int(updated_ms))},
    }}
    try:
        r = requests.patch(f"{_FS}/users/{uid}",
                           headers={"Authorization": "Bearer " + id_token},
                           json=body, timeout=TIMEOUT)
    except requests.RequestException:
        raise SyncError("Couldn't reach the sync server.")
    if r.status_code != 200:
        raise SyncError("Couldn't save your data to the server.")
    return True
