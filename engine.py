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
import re
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


SITE = {"x": "X", "tiktok": "TikTok", "snapchat": "Snapchat", "instagram": "Instagram",
        "facebook": "Facebook"}


def normalize_facebook_url(final):
    """Turn whatever a shared link opened into a link the engine can download."""
    import re
    from urllib.parse import urlsplit, parse_qs
    m = re.search(r"/(?:reel|reels)/(\d+)", final) or re.search(r"[?&]v=(\d+)", final) \
        or re.search(r"/videos/(?:[^/?#]+/)?(\d+)", final)
    if m:
        return f"https://www.facebook.com/watch/?v={m.group(1)}"
    parts = urlsplit(final)
    path = parts.path.rstrip("/")
    if re.search(r"/(photo|photo\.php|media/set)", path):
        album = parse_qs(parts.query).get("set", [""])[0]
        if album:
            return f"https://www.facebook.com/media/set/?set={album}"
        return final.replace("facebook.com/photo.php?", "facebook.com/photo/?")
    if path.endswith("profile.php"):
        pid = parse_qs(parts.query).get("id", [""])[0]
        if pid:
            return f"https://www.facebook.com/{pid}/photos"
    m = re.fullmatch(r"/(?:people/[^/]+/)?([\w.-]+)", path)
    if m and m.group(1) not in ("share", "watch", "login", "login.php"):
        return f"https://www.facebook.com/{m.group(1)}/photos"
    return final


def resolve_facebook_share(url, cookies=None):
    """Follow a facebook.com/share/... or fb.watch link to the real profile/post."""
    import re
    import requests
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    r = requests.get(url, headers=headers, cookies=cookies or {},
                     allow_redirects=True, timeout=30)
    final = r.url
    if re.search(r"facebook\.com/share/|fb\.watch/", final):
        # Not forwarded: the page still names the real address.
        html = r.text
        m = (re.search(r'property="og:url"\s+content="([^"]+)"', html)
             or re.search(r'rel="canonical"\s+href="([^"]+)"', html))
        if m:
            final = m.group(1).replace("&amp;", "&")
    return normalize_facebook_url(final)


def friendly_error(rc, site, detail=""):
    name = SITE.get(site, "The site")
    d = detail.lower()
    if site == "tiktok" and ("could not extract any" in d or "need cookies" in d
                             or "provide valid cookies" in d):
        return ("TikTok didn't show this profile's videos. Log in to TikTok in the app "
                "(TikTok account → Log in), then try again.")
    if site == "facebook" and ("photo list" in d or "set_id" in d):
        return ("Facebook didn't show this profile's photo list. Open one of their photos, "
                "tap Share → Copy link, and paste that link: the app downloads that whole album.")
    if site == "facebook" and "didn't show this profile's photos" in d:
        return ("Facebook didn't show this profile's photos. Make sure you're logged in "
                "to Facebook in the app; if you are, the photos may not be public.")
    if site == "facebook" and "temporarily blocked" in d:
        return "Facebook temporarily blocked viewing. Wait a few hours or use another account."
    if site == "facebook" and ("unsupported url" in d or "no suitable extractor" in d):
        return ("This kind of Facebook link isn't supported. Use a profile/page, photo, "
                "album, video or reel link.")
    if site == "tiktok" and ("403" in d or "captcha" in d or "verify" in d or (rc and rc & 8)):
        return ("TikTok blocked the request. Log in to TikTok in the app "
                "(or wait a few hours), then try again.")
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
    def __init__(self, spec, scan=None, notify=None, pos=(1, 1)):
        self.spec = spec
        self.pos = pos
        self.prefix = f"[{pos[0]}/{pos[1]}] " if pos[1] > 1 else ""
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
        return self.prefix + self._progress_text()

    def _progress_text(self):
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
                self.notify(f"{self.prefix}@{self.spec['user']}: {self._progress_text()}")
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
        while self.state["state"] == "running" and not getattr(self, "result", None):
            time.sleep(3)
            if self.state["state"] == "running" and not getattr(self, "result", None):
                self.save()

    # ---- run ----
    def run(self, final=True):
        first, n = self.pos[0] == 1, self.pos[1]
        if first:
            open(self.log_path, "w").close()
        if n > 1:
            self.log(("" if first else "\n") + f"── {self.prefix}{SITE.get(self.spec['site'], '')} @{self.spec['user']} ──")
        self.save(state="running", count=0, total=0, seen=0,
                  status=f"{self.prefix}Starting @{self.spec['user']}…")
        self.notify(f"{self.prefix}Downloading @{self.spec['user']}…")
        threading.Thread(target=self._heartbeat, daemon=True).start()
        self.log("Fetching media list…")
        self.save(status=f"{self.prefix}Getting the list from {SITE.get(self.spec['site'], 'the site')}… "
                         "big profiles can take a few minutes")

        import gallery_dl
        import snapext
        snapext.register()
        rc, result = 1, "error"
        old = sys.stdout, sys.stderr, sys.argv
        logging.getLogger().handlers.clear()
        gallery_dl.config.clear()
        writer = _LineWriter(self.on_line)
        sys.stdout = sys.stderr = writer
        args = list(self.spec["args"])
        if self.spec["site"] == "facebook" and re.search(r"fb\.watch/|facebook\.com/share/", args[-1]):
            try:
                cookies = next((json.loads(a[8:]) for a in args if a.startswith("cookies=")), None)
                real = resolve_facebook_share(args[-1], cookies)
                self.log(f"Shared link opens: {real}")
                args[-1] = real
                # Save under the real name instead of "shared"
                m = re.fullmatch(r"https://www\.facebook\.com/([\w.-]+)/photos", real)
                shared_dir = self.spec["out"]
                if m and os.path.basename(shared_dir) == "shared":
                    new_dir = os.path.join(os.path.dirname(shared_dir), m.group(1))
                    os.makedirs(new_dir, exist_ok=True)
                    args = [a.replace(shared_dir, new_dir)
                            if isinstance(a, str) and a.startswith(shared_dir) else a
                            for a in args]
                    self.spec["out"] = new_dir
                    self.spec["user"] = m.group(1)
                    if self.spec.get("out_label"):
                        self.spec["out_label"] = os.path.join(
                            os.path.dirname(self.spec["out_label"]), m.group(1))
                    try:
                        os.rmdir(shared_dir)
                    except OSError:
                        pass
            except Exception as e:
                self.log(f"Couldn't open the shared link: {e}")
        sys.argv = ["gallery-dl"] + args
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

        if (result == "done" and self.spec["site"] == "tiktok" and self.count == 0
                and not self.posts and "could not extract any" in self.last_error.lower()):
            result = "error"
        if (result == "done" and self.spec["site"] == "facebook" and self.count == 0
                and "didn't show this profile's photos" in self.last_error):
            result = "error"
        if (result == "done" and self.spec["site"] == "facebook" and self.count == 0
                and "photo list" in self.last_error.lower()):
            result = "error"

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
        self.result, self.result_text = result, text
        if final:
            self.save(state=result, count=self.count, total=self.total, seen=len(self.posts),
                      status=f"{text} {self.count} new files")
            self.notify(f"{text} {self.count} new files from @{self.spec['user']}", done=True)
        return result


def run_queue(queue, scan=None, notify=None):
    """Download several links one after another.

    queue = {"jobs": [spec, ...]}  (a single spec dict is also accepted)
    Returns the overall result: "done", "error" or "cancelled".
    """
    jobs = queue.get("jobs") or [queue]
    n = len(jobs)
    if n == 1:
        return Job(jobs[0], scan=scan, notify=notify).run()

    results, total_new, last = [], 0, None
    for i, spec in enumerate(jobs, 1):
        last = Job(spec, scan=scan, notify=notify, pos=(i, n))
        if last.cancelled():
            break
        r = last.run(final=False)
        results.append(r)
        total_new += last.count
        if r == "cancelled":
            break

    problems = results.count("error")
    if "cancelled" in results or len(results) < n:
        state, head = "cancelled", f"Stopped after {len(results)} of {n} links."
    elif problems:
        state, head = "error", f"Finished {n} links, {problems} with problems."
    else:
        state, head = "done", f"Finished all {n} links."
    status = f"{head} {total_new} new files"
    last.log("\n" + status)
    last.save(state=state, count=total_new, total=0, seen=0, status=status)
    last.notify(status, done=True)
    return state
