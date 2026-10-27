"""
Download engine shared by the app (desktop) and the Android background service.

A job is described by a dict:
    {"args": [...gallery-dl args...], "out": folder, "site": "x"|"tiktok",
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
    def on_line(self, line):
        if self.cancelled():
            raise SystemExit("cancelled")
        if line.startswith("#"):
            self.log("skip " + os.path.basename(line[1:].strip()))
        elif not line.startswith("[") and os.sep in line:
            self.count += 1
            self.new_files.append(line.strip())
            if len(self.new_files) >= 25:
                self.flush_scan()
            self.log("+ " + os.path.basename(line))
            self.save(count=self.count, status=f"{self.count} files downloaded…")
            if time.time() - self._last_notify > 2:
                self._last_notify = time.time()
                self.notify(f"@{self.spec['user']}: {self.count} files downloaded")
        else:
            self.log(line)
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
                "error": f"Finished with errors (code {rc})."}[result]
        self.log(f"Saved in: {self.spec.get('out_label', self.spec['out'])}")
        self.save(state=result, count=self.count, status=f"{text} {self.count} new files")
        self.notify(f"{text} {self.count} new files from @{self.spec['user']}", done=True)
        return result
