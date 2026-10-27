"""
Download engine shared by the app (desktop) and the Android background service.

A job is described by a dict:
    {"args": [...gallery-dl args...], "out": folder, "site": "x"|"tiktok"|"snapchat",
     "user": name, "status_dir": folder for job.json / job.log / cancel}

Progress is written to files in status_dir so the app can show it even when
the download runs in a separate background process.
"""

import io
import json
import logging
import os
import sys
import threading
import time

MEDIA_EXT = (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".m4v", ".webm")


def paths(status_dir):
    return (os.path.join(status_dir, "job.json"),
            os.path.join(status_dir, "job.log"),
            os.path.join(status_dir, "cancel"))


def read_status(status_dir):
    try:
        with open(paths(status_dir)[0], encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def request_cancel(status_dir):
    try:
        open(paths(status_dir)[2], "w").close()
    except Exception:
        pass


SITE = {"x": "X", "tiktok": "TikTok", "snapchat": "Snapchat"}


def friendly_error(rc, site, detail=""):
    name = SITE.get(site, "The site")
    d = detail.lower()
    if rc and rc & 16 or "401" in d or "login" in d or "auth" in d:
        return f"{name} needs you to be logged in."
    if "404" in d or "not found" in d or "does not exist" in d or "suspended" in d:
        return "Profile or post not found (check the link)."
    if "429" in d or "rate limit" in d:
        return f"{name} is limiting requests. Try again in 15 minutes."
    if "403" in d or (rc and rc & 8):
        return f"{name} blocked the request. Try again later."
    if "private" in d or "protected" in d:
        return "This account is private."
    if rc and rc & 4:
        return f"{name} refused the request or the content is unavailable."
    return f"Something went wrong (code {rc})."


class _LineWriter(io.TextIOBase):
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


class Job:
    def __init__(self, spec, scan=None, notify=None):
        self.spec = spec
        self.scan = scan or (lambda paths: None)
        self.notify = notify or (lambda text, done=False: None)
        os.makedirs(spec["status_dir"], exist_ok=True)
        self.status_path, self.log_path, self.cancel_path = paths(spec["status_dir"])
        self.count = 0
        self.new_files = []
        self.total = 0
        self.last_error = ""
        self.posts = set()
        self.state = {"state": "running", "count": 0, "site": spec["site"],
                      "user": spec["user"], "out": spec["out"],
                      "started": time.time(), "beat": time.time(),
                      "status": "Starting…"}
        self._lock = threading.Lock()
        self._last_notify = 0

    # ---- status/log files ----
    def save(self, **kw):
        with self._lock:
            self.state.update(kw)
            self.state["beat"] = time.time()
            tmp = self.status_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f)
            os.replace(tmp, self.status_path)

    def log(self, line):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def cancelled(self):
        return os.path.exists(self.cancel_path)

    # ---- gallery-dl output ----
    def progress_text(self):
        seen = len(self.posts)
        if self.total and seen <= self.total:
            return f"{self.count} new · post {seen} of ~{self.total} · ≈{self.total - seen} left"
        if seen:
            return f"{self.count} new · {seen} posts checked"
        return f"{self.count} files downloaded…"

    def on_line(self, line):
        if self.cancelled():
            raise SystemExit("cancelled")
        if line.startswith("@@META "):
            total, _, post_id = line[7:].partition("|")
            try:
                self.total = max(self.total, int(total))
            except ValueError:
                pass
            if post_id and post_id not in self.posts:
                self.posts.add(post_id)
                if len(self.posts) % 5 == 0 or len(self.posts) == 1:
                    self.save(total=self.total, seen=len(self.posts), status=self.progress_text())
            return
        if line.startswith("#"):
            self.log("skip " + os.path.basename(line[1:].strip()))
        elif not line.startswith("[") and os.sep in line:
            self.count += 1
            self.new_files.append(line.strip())
            if len(self.new_files) >= 25:
                self.flush_scan()
            self.log("+ " + os.path.basename(line))
            self.save(count=self.count, total=self.total, seen=len(self.posts),
                      status=self.progress_text())
            if time.time() - self._last_notify > 2:
                self._last_notify = time.time()
                self.notify(f"@{self.spec['user']}: {self.progress_text()}")
        else:
            self.log(line)
            if "][error]" in line or "][warning]" in line:
                self.last_error = line.split("] ", 1)[-1].strip()
            low = line.lower()
            if self.spec["site"] == "x" and (
                    "401" in low or "login" in low or "authenticat" in low):
                self.log("→ Tap Log in to X first.")

    def flush_scan(self):
        batch, self.new_files = self.new_files, []
        if batch:
            try:
                self.scan(batch)
            except Exception as e:
                self.log(f"Gallery scan issue: {e}")

    def _heartbeat(self):
        while self.state["state"] == "running":
            time.sleep(3)
            if self.state["state"] == "running":
                self.save()

    # ---- run ----
    def run(self):
        try:
            os.remove(self.cancel_path)
        except OSError:
            pass
        open(self.log_path, "w").close()
        self.save()
        self.notify(f"Downloading @{self.spec['user']}…")
        threading.Thread(target=self._heartbeat, daemon=True).start()
        self.log("Fetching media list…")

        import gallery_dl
        import snapext
        snapext.register()
        rc, result = 1, "error"
        old = sys.stdout, sys.stderr, sys.argv
        logging.getLogger().handlers.clear()
        gallery_dl.config.clear()
        writer = _LineWriter(self.on_line)
        sys.stdout = sys.stderr = writer
        sys.argv = ["gallery-dl"] + list(self.spec["args"])
        try:
            rc = gallery_dl.main() or 0
            result = "done" if rc == 0 else "error"
        except SystemExit as e:
            if self.cancelled():
                result = "cancelled"
            else:
                rc = e.code or 0
                result = "done" if rc == 0 else "error"
        except Exception as e:
            self.log(f"Error: {e}")
        finally:
            sys.stdout, sys.stderr, sys.argv = old

        self.flush_scan()
        try:
            found = []
            for dirpath, _, files in os.walk(self.spec["out"]):
                found += [os.path.join(dirpath, f) for f in files
                          if f.lower().endswith(MEDIA_EXT)]
            self.scan(found)
        except Exception as e:
            self.log(f"Gallery scan issue: {e}")

        text = {"done": "Finished.", "cancelled": "Stopped.",
                "error": "Problem: " + friendly_error(rc, self.spec["site"], self.last_error)}[result]
        if result == "error" and self.last_error:
            self.log("Reason: " + self.last_error)
        self.log(f"Saved in: {self.spec.get('out_label', self.spec['out'])}")
        self.save(state=result, count=self.count, total=self.total, seen=len(self.posts),
                  status=f"{text} {self.count} new files")
        self.notify(f"{text} {self.count} new files from @{self.spec['user']}", done=True)
        return result
