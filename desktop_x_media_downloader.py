#!/usr/bin/env python3
"""
X Media Downloader
------------------
Paste the URL of a public X (Twitter) profile, pick a folder, and download
the photos / videos / GIFs from that profile's Media tab.

Uses gallery-dl under the hood (installed automatically on first run).
X only shows a profile's media to logged-in users, so the app borrows the
login cookies from a browser where you're signed in to x.com (or a
cookies.txt file you exported).
"""

import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

RESERVED = {
    "home", "explore", "search", "notifications", "messages", "i", "settings",
    "compose", "login", "logout", "signup", "tos", "privacy", "hashtag", "intent",
}
BROWSERS = ["chrome", "firefox", "edge", "brave", "safari", "opera", "vivaldi", "chromium"]
IMAGE_EXT = ("jpg", "jpeg", "png", "webp")
VIDEO_EXT = ("mp4", "mov", "m4v", "webm")


# ---------- helpers ----------

def parse_username(text: str) -> str | None:
    """Accept 'https://x.com/user', 'twitter.com/user/media', '@user' or 'user'."""
    text = text.strip()
    m = re.match(r"^(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/([^/?#]+)", text, re.I)
    name = m.group(1) if m else text.lstrip("@")
    if re.fullmatch(r"[A-Za-z0-9_]{1,15}", name) and name.lower() not in RESERVED:
        return name
    return None


def ensure_gallery_dl() -> bool:
    try:
        import gallery_dl  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "gallery-dl"])
        return True
    except Exception:
        try:  # some Linux distros need this flag
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade",
                                   "--break-system-packages", "gallery-dl"])
            return True
        except Exception:
            return False


def build_command(user, out_dir, cookie_mode, cookie_value, want_images, want_videos, retweets):
    exts = (IMAGE_EXT if want_images else ()) + (VIDEO_EXT if want_videos else ())
    archive = os.path.join(out_dir, user, ".downloaded.sqlite3")
    cmd = [
        sys.executable, "-m", "gallery_dl",
        "-d", out_dir,
        "--download-archive", archive,       # re-runs only fetch new posts
        "-o", f"retweets={'true' if retweets else 'false'}",
        "-o", "videos=true",
        "--filter", f"extension in {exts!r}",
    ]
    if cookie_mode == "browser":
        cmd += ["--cookies-from-browser", cookie_value]
    elif cookie_mode == "file":
        cmd += ["--cookies", cookie_value]
    cmd.append(f"https://x.com/{user}/media")
    return cmd


# ---------- GUI ----------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("X Media Downloader")
        self.geometry("720x520")
        self.minsize(560, 420)
        self.proc = None
        self.logq: queue.Queue = queue.Queue()
        self.count = 0
        self._build()
        self.after(100, self._drain_log)

    def _build(self):
        pad = {"padx": 10, "pady": 4}
        frm = ttk.Frame(self)
        frm.pack(fill="x", **pad)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Profile URL:").grid(row=0, column=0, sticky="w")
        self.url = tk.StringVar()
        ent = ttk.Entry(frm, textvariable=self.url)
        ent.grid(row=0, column=1, columnspan=2, sticky="ew", pady=3)
        ent.focus()

        ttk.Label(frm, text="Save to:").grid(row=1, column=0, sticky="w")
        self.out = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads", "X-media"))
        ttk.Entry(frm, textvariable=self.out).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(frm, text="Browse…", command=self._pick_dir).grid(row=1, column=2, padx=(6, 0))

        ttk.Label(frm, text="Login cookies:").grid(row=2, column=0, sticky="w")
        ck = ttk.Frame(frm)
        ck.grid(row=2, column=1, columnspan=2, sticky="ew", pady=3)
        self.cookie_mode = tk.StringVar(value="browser")
        ttk.Radiobutton(ck, text="From browser", variable=self.cookie_mode, value="browser").pack(side="left")
        self.browser = tk.StringVar(value="chrome")
        ttk.Combobox(ck, textvariable=self.browser, values=BROWSERS, width=10,
                     state="readonly").pack(side="left", padx=(4, 14))
        ttk.Radiobutton(ck, text="cookies.txt", variable=self.cookie_mode, value="file").pack(side="left")
        self.cookie_file = tk.StringVar()
        ttk.Entry(ck, textvariable=self.cookie_file, width=18).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(ck, text="…", width=3, command=self._pick_cookie).pack(side="left")
        ttk.Radiobutton(ck, text="None", variable=self.cookie_mode, value="none").pack(side="left", padx=(10, 0))

        opts = ttk.Frame(frm)
        opts.grid(row=3, column=1, columnspan=2, sticky="w", pady=3)
        self.images = tk.BooleanVar(value=True)
        self.videos = tk.BooleanVar(value=True)
        self.retweets = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Photos", variable=self.images).pack(side="left")
        ttk.Checkbutton(opts, text="Videos & GIFs", variable=self.videos).pack(side="left", padx=12)
        ttk.Checkbutton(opts, text="Include reposts", variable=self.retweets).pack(side="left")

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10)
        self.go = ttk.Button(btns, text="Download", command=self.start)
        self.go.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Open folder", command=self._open_folder).pack(side="left")
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(btns, textvariable=self.status).pack(side="right")

        self.log = tk.Text(self, height=16, wrap="none", font=("Courier", 9))
        self.log.pack(fill="both", expand=True, padx=10, pady=8)
        self.bind("<Return>", lambda e: self.start())

    # --- small actions ---
    def _pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.out.get() or os.path.expanduser("~"))
        if d:
            self.out.set(d)

    def _pick_cookie(self):
        f = filedialog.askopenfilename(filetypes=[("Cookies", "*.txt"), ("All files", "*.*")])
        if f:
            self.cookie_file.set(f)
            self.cookie_mode.set("file")

    def _open_folder(self):
        user = parse_username(self.url.get())
        path = os.path.join(self.out.get(), "twitter", user) if user else self.out.get()
        if not os.path.isdir(path):
            path = self.out.get()
        os.makedirs(path, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def _write(self, line):
        self.log.insert("end", line + "\n")
        self.log.see("end")

    # --- download ---
    def start(self):
        if self.proc:
            return
        user = parse_username(self.url.get())
        if not user:
            messagebox.showerror("Invalid URL", "Paste a profile link like https://x.com/username")
            return
        if not (self.images.get() or self.videos.get()):
            messagebox.showerror("Nothing selected", "Tick Photos and/or Videos.")
            return
        mode = self.cookie_mode.get()
        cval = self.browser.get() if mode == "browser" else self.cookie_file.get()
        if mode == "file" and not os.path.isfile(cval):
            messagebox.showerror("Cookies", "Choose a valid cookies.txt file.")
            return
        out = self.out.get()
        os.makedirs(os.path.join(out, user), exist_ok=True)

        self.log.delete("1.0", "end")
        self.count = 0
        self.go.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status.set(f"Downloading @{user}…")
        cmd = build_command(user, out, mode, cval, self.images.get(), self.videos.get(), self.retweets.get())
        threading.Thread(target=self._run, args=(cmd,), daemon=True).start()

    def _run(self, cmd):
        if not ensure_gallery_dl():
            self.logq.put(("done", "Could not install gallery-dl. Run: pip install gallery-dl"))
            return
        self.logq.put(("log", "Fetching media list… (large profiles take a while)"))
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, bufsize=1, encoding="utf-8", errors="replace")
            for line in self.proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                if line.startswith("#"):          # already downloaded / skipped
                    self.logq.put(("log", "skip  " + line[1:].strip()))
                elif not line.startswith("[") and (os.sep in line or "/" in line):
                    self.logq.put(("file", line))
                else:
                    self.logq.put(("log", line))
            rc = self.proc.wait()
            self.logq.put(("done", "Finished." if rc == 0 else f"Stopped / finished with errors (code {rc})."))
        except Exception as e:
            self.logq.put(("done", f"Error: {e}"))

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _drain_log(self):
        try:
            while True:
                kind, msg = self.logq.get_nowait()
                if kind == "file":
                    self.count += 1
                    self._write(f"✓ {os.path.basename(msg)}")
                    self.status.set(f"{self.count} files downloaded…")
                elif kind == "log":
                    self._write(msg)
                    if "401" in msg or "login" in msg.lower() or "authenticat" in msg.lower():
                        self._write("  → X needs you to be logged in. Sign in to x.com in the "
                                    "selected browser (and close it), or use a cookies.txt file.")
                elif kind == "done":
                    self._write(msg)
                    self.status.set(f"{msg}  {self.count} new files.")
                    self.proc = None
                    self.go.config(state="normal")
                    self.stop_btn.config(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain_log)


# ---------- command-line mode ----------
def cli(argv):
    import argparse
    p = argparse.ArgumentParser(description="Download media from a public X profile.")
    p.add_argument("url")
    p.add_argument("-o", "--out", default=os.path.join(os.path.expanduser("~"), "Downloads", "X-media"))
    p.add_argument("--browser", default="chrome", help="browser to read login cookies from")
    p.add_argument("--cookies", help="cookies.txt file instead of a browser")
    p.add_argument("--no-images", action="store_true")
    p.add_argument("--no-videos", action="store_true")
    p.add_argument("--retweets", action="store_true")
    a = p.parse_args(argv)
    user = parse_username(a.url)
    if not user:
        sys.exit("Invalid profile URL.")
    if not ensure_gallery_dl():
        sys.exit("Could not install gallery-dl.")
    mode, val = ("file", a.cookies) if a.cookies else ("browser", a.browser)
    os.makedirs(os.path.join(a.out, user), exist_ok=True)
    cmd = build_command(user, a.out, mode, val, not a.no_images, not a.no_videos, a.retweets)
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cli(sys.argv[1:])
    else:
        App().mainloop()
