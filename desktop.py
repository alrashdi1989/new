"""
Media Downloader — desktop (Windows) version.

Same download engine as the phone app (engine.py, links.py, ytjob.py,
storyjoin.py, snapext.py, threadsext.py), with a Tkinter window. On a PC it
uses ffmpeg directly and can reuse the logins from your web browser, so there's
no in-app sign-in to do.
"""

import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import engine
import links as L

APP_VERSION = "1.0"

# ---- where ffmpeg and our config live -------------------------------------

def _bundle_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _ensure_ffmpeg_on_path():
    """A bundled ffmpeg.exe sits next to the program; make sure tools find it."""
    d = _bundle_dir()
    exe = os.path.join(d, "ffmpeg.exe")
    if os.path.exists(exe):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        return exe
    return "ffmpeg"          # fall back to one on PATH


FFMPEG = _ensure_ffmpeg_on_path()


def config_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "MediaDownloader")
    os.makedirs(d, exist_ok=True)
    return d


SETTINGS = os.path.join(config_dir(), "settings.json")


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


# ---- building jobs (mirrors the phone app's build_jobs) -------------------

BROWSERS = ["(no login)", "chrome", "edge", "firefox", "brave", "opera", "vivaldi"]


def build_jobs(found, opt):
    save_dir = opt["save_dir"]
    browser = opt.get("browser")
    content = opt["content"]
    jobs = []
    for site, user, gurl in found:
        out = L.output_dir(save_dir, site, user)
        if site == "youtube":
            jobs.append({"args": [], "out": out, "out_label": out, "site": site, "user": user,
                         "yt": {"url": gurl, "audio_only": not opt["videos"],
                                "ffmpeg_location": FFMPEG,
                                "cookiesfrombrowser": (browser,) if browser else None}})
            continue
        if site == "web":
            gdl_out = os.path.join(out, "Images")
            jobs.append({
                "args": ["-d", gdl_out, "--download-archive",
                         os.path.join(gdl_out, ".downloaded.sqlite3"),
                         "-o", "path-restrict=windows", "-o", "path-strip=windows", gurl],
                "out": out, "out_label": out, "site": site, "user": user,
                "yt": {"url": gurl, "audio_only": not opt["videos"], "format": L.WEB_FORMAT_HINT,
                       "name": user, "folder": "%(extractor_key)s", "ffmpeg_location": FFMPEG,
                       "cookiesfrombrowser": (browser,) if browser else None}})
            continue
        args = L.build_args(site, gurl, out, opt.get("token", ""), opt["photos"], opt["videos"],
                            opt["reposts"], None, None, content, rename=opt["rename"])
        if browser:                       # reuse the browser's logins for this site
            args += ["--cookies-from-browser", browser]
        jobs.append({"args": args, "out": out, "out_label": out, "site": site, "user": user,
                     "rename": opt["rename"],
                     "join_stories": "join" in content and "stories" in content,
                     "story_only_combined": opt.get("only_combined", False)})
    for i, j in enumerate(jobs, 1):
        j["pos"] = [i, len(jobs)]
    return jobs


# ---- the window -----------------------------------------------------------

BG = "#12142a"
CARD = "#1b1e3a"
FG = "#ffffff"
MUT = "#9aa0c0"
ACC = "#4fd0ff"


class App:
    def __init__(self, root):
        self.root = root
        self.s = load_settings()
        self.status_dir = os.path.join(config_dir(), "job")
        os.makedirs(self.status_dir, exist_ok=True)
        self.running = False
        self._build_ui()
        self._restore()

    # ---------- UI
    def _build_ui(self):
        r = self.root
        r.title(f"Media Downloader {APP_VERSION}")
        r.geometry("720x760")
        r.configure(bg=BG)
        r.minsize(600, 600)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TProgressbar", troughcolor=CARD, background=ACC, thickness=10)

        nb = ttk.Notebook(r)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        self.tab_dl = tk.Frame(nb, bg=BG)
        self.tab_fav = tk.Frame(nb, bg=BG)
        nb.add(self.tab_dl, text="Download")
        nb.add(self.tab_fav, text="Favorites")
        self._build_download(self.tab_dl)
        self._build_favorites(self.tab_fav)

    def _label(self, parent, text, **kw):
        return tk.Label(parent, text=text, bg=kw.pop("bg", BG), fg=kw.pop("fg", MUT),
                        anchor="w", **kw)

    def _build_download(self, p):
        self._label(p, "Paste one or more links (X, TikTok, Snapchat, Instagram, Threads, "
                       "Facebook, YouTube, or any website):").pack(fill="x", padx=12, pady=(12, 2))
        self.links = tk.Text(p, height=4, bg=CARD, fg=FG, insertbackground=FG,
                             relief="flat", wrap="word")
        self.links.pack(fill="x", padx=12)

        opts = tk.Frame(p, bg=BG)
        opts.pack(fill="x", padx=12, pady=8)
        self.v_photos = tk.BooleanVar(value=True)
        self.v_videos = tk.BooleanVar(value=True)
        self.v_reposts = tk.BooleanVar(value=False)
        self.v_story = tk.BooleanVar(value=True)
        self.v_high = tk.BooleanVar(value=True)
        self.v_posts = tk.BooleanVar(value=True)
        self.v_spot = tk.BooleanVar(value=True)
        self.v_join = tk.BooleanVar(value=False)
        self.v_only = tk.BooleanVar(value=False)
        self.v_rename = tk.BooleanVar(value=True)
        checks = [("Photos", self.v_photos), ("Videos", self.v_videos), ("X reposts", self.v_reposts),
                  ("Stories", self.v_story), ("Highlights", self.v_high), ("Posts", self.v_posts),
                  ("Spotlight", self.v_spot)]
        for i, (t, v) in enumerate(checks):
            self._check(opts, t, v).grid(row=i // 4, column=i % 4, sticky="w", padx=4, pady=2)
        row2 = tk.Frame(p, bg=BG)
        row2.pack(fill="x", padx=12)
        self._check(row2, "Stories as one video", self.v_join).pack(side="left")
        self._check(row2, "keep only the combined video", self.v_only).pack(side="left", padx=12)
        self._check(row2, "Name files profile + time", self.v_rename).pack(side="left", padx=12)

        folder = tk.Frame(p, bg=BG)
        folder.pack(fill="x", padx=12, pady=8)
        self._label(folder, "Save to:").pack(side="left")
        self.v_folder = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads",
                                                         "MediaDownloader"))
        tk.Entry(folder, textvariable=self.v_folder, bg=CARD, fg=FG, relief="flat",
                 insertbackground=FG).pack(side="left", fill="x", expand=True, padx=6)
        tk.Button(folder, text="Browse", command=self._pick_folder, bg=CARD, fg=FG,
                  relief="flat").pack(side="left")

        login = tk.Frame(p, bg=BG)
        login.pack(fill="x", padx=12)
        self._label(login, "Use logins from browser:").pack(side="left")
        self.v_browser = tk.StringVar(value=BROWSERS[0])
        ttk.Combobox(login, textvariable=self.v_browser, values=BROWSERS, state="readonly",
                     width=14).pack(side="left", padx=6)
        self._label(login, "(needed for private / age-restricted accounts)", fg=MUT).pack(side="left")

        btns = tk.Frame(p, bg=BG)
        btns.pack(fill="x", padx=12, pady=10)
        self.btn = tk.Button(btns, text="Download", command=self._on_download, bg=ACC,
                             fg="#042330", relief="flat", font=("Segoe UI", 11, "bold"),
                             height=1)
        self.btn.pack(side="left", ipadx=20)
        tk.Button(btns, text="Clear", command=lambda: self.links.delete("1.0", "end"),
                  bg=CARD, fg=FG, relief="flat").pack(side="left", padx=8)

        self.prog = ttk.Progressbar(p, mode="determinate", maximum=1.0)
        self.prog.pack(fill="x", padx=12, pady=(4, 2))
        self.v_status = tk.StringVar(value="Ready.")
        self._label(p, "", textvariable=self.v_status, fg=ACC).pack(fill="x", padx=12)

        logbar = tk.Frame(p, bg=BG)
        logbar.pack(fill="x", padx=12, pady=(8, 0))
        self._label(logbar, "Log").pack(side="left")
        tk.Button(logbar, text="Copy log", command=self._copy_log, bg=CARD, fg=FG,
                  relief="flat").pack(side="right")
        self.log = tk.Text(p, height=10, bg="#0c0e20", fg=MUT, relief="flat", wrap="word")
        self.log.pack(fill="both", expand=True, padx=12, pady=(2, 12))

    def _check(self, parent, text, var):
        return tk.Checkbutton(parent, text=text, variable=var, bg=BG, fg=FG, selectcolor=CARD,
                              activebackground=BG, activeforeground=FG, relief="flat",
                              highlightthickness=0, command=self._save)

    def _build_favorites(self, p):
        self._label(p, "Saved profiles — select and click \"Get new\" to download anything new:"
                    ).pack(fill="x", padx=12, pady=(12, 4))
        self.fav_list = tk.Listbox(p, bg=CARD, fg=FG, relief="flat", selectmode="extended",
                                   height=16, highlightthickness=0)
        self.fav_list.pack(fill="both", expand=True, padx=12)
        bar = tk.Frame(p, bg=BG)
        bar.pack(fill="x", padx=12, pady=10)
        tk.Button(bar, text="Add from the links above", command=self._fav_add, bg=CARD, fg=FG,
                  relief="flat").pack(side="left")
        tk.Button(bar, text="Remove", command=self._fav_remove, bg=CARD, fg=FG,
                  relief="flat").pack(side="left", padx=6)
        tk.Button(bar, text="Get new (all)", command=lambda: self._fav_get(False), bg=ACC,
                  fg="#042330", relief="flat", font=("Segoe UI", 10, "bold")).pack(side="right")
        tk.Button(bar, text="Get new (selected)", command=lambda: self._fav_get(True), bg=CARD,
                  fg=FG, relief="flat").pack(side="right", padx=6)

    # ---------- settings
    def _restore(self):
        s = self.s
        self.v_folder.set(s.get("save_dir", self.v_folder.get()))
        self.v_browser.set(s.get("browser", BROWSERS[0]))
        for key, var in (("photos", self.v_photos), ("videos", self.v_videos),
                         ("reposts", self.v_reposts), ("story", self.v_story),
                         ("high", self.v_high), ("posts", self.v_posts), ("spot", self.v_spot),
                         ("join", self.v_join), ("only", self.v_only), ("rename", self.v_rename)):
            if key in s:
                var.set(s[key])
        for link in s.get("favorites", []):
            self.fav_list.insert("end", link)

    def _save(self):
        self.s.update({
            "save_dir": self.v_folder.get(), "browser": self.v_browser.get(),
            "photos": self.v_photos.get(), "videos": self.v_videos.get(),
            "reposts": self.v_reposts.get(), "story": self.v_story.get(),
            "high": self.v_high.get(), "posts": self.v_posts.get(), "spot": self.v_spot.get(),
            "join": self.v_join.get(), "only": self.v_only.get(), "rename": self.v_rename.get(),
            "favorites": list(self.fav_list.get(0, "end")),
        })
        save_settings(self.s)

    def _pick_folder(self):
        d = filedialog.askdirectory(initialdir=self.v_folder.get() or os.path.expanduser("~"))
        if d:
            self.v_folder.set(d)
            self._save()

    def _content(self):
        c = set()
        if self.v_story.get():
            c.add("stories")
        if self.v_high.get():
            c.add("highlights")
        if self.v_posts.get():
            c.add("posts")
        if self.v_spot.get():
            c.add("spotlight")
        if self.v_join.get():
            c.add("join")
        return c

    def _opts(self):
        browser = self.v_browser.get()
        return {
            "save_dir": self.v_folder.get().strip() or os.path.expanduser("~"),
            "browser": None if browser == BROWSERS[0] else browser,
            "photos": self.v_photos.get(), "videos": self.v_videos.get(),
            "reposts": self.v_reposts.get(), "rename": self.v_rename.get(),
            "only_combined": self.v_join.get() and self.v_only.get(),
            "content": self._content(),
        }

    # ---------- favorites
    def _fav_add(self):
        found, _ = L.parse_links(self.links.get("1.0", "end"))
        existing = set(self.fav_list.get(0, "end"))
        added = 0
        for _site, _user, _url in found:
            raw = self._profile_link(_site, _user, _url)
            if raw and raw not in existing:
                self.fav_list.insert("end", raw)
                existing.add(raw)
                added += 1
        self._save()
        self.v_status.set(f"Added {added} to favorites." if added else "Nothing new to add.")

    @staticmethod
    def _profile_link(site, user, url):
        return url if url.startswith("http") or url.startswith(("snapfull:", "threads:")) else url

    def _fav_remove(self):
        for i in reversed(self.fav_list.curselection()):
            self.fav_list.delete(i)
        self._save()

    def _fav_get(self, selected):
        items = ([self.fav_list.get(i) for i in self.fav_list.curselection()] if selected
                 else list(self.fav_list.get(0, "end")))
        if not items:
            messagebox.showinfo("Favorites", "No favorites to download.")
            return
        self._start("\n".join(items))

    # ---------- download
    def _on_download(self):
        if self.running:
            engine.request_cancel(self.status_dir)
            self.v_status.set("Stopping…")
            return
        self._start(self.links.get("1.0", "end"))

    def _start(self, text):
        found, bad = L.parse_links(text)
        if not found:
            self.v_status.set("Paste a link first.")
            return
        if not (self.v_photos.get() or self.v_videos.get()):
            self.v_status.set("Pick Photos and/or Videos.")
            return
        self._save()
        opt = self._opts()
        try:
            os.makedirs(opt["save_dir"], exist_ok=True)
        except OSError as e:
            messagebox.showerror("Folder", f"Can't use that folder:\n{e}")
            return
        jobs = build_jobs(found, opt)
        for j in jobs:
            j["status_dir"] = self.status_dir
            os.makedirs(j["out"], exist_ok=True)
        spec = {"jobs": jobs, "status_dir": self.status_dir, "lane": 1,
                "lanes": [self.status_dir]}
        # clear old progress
        for f in engine.paths(self.status_dir) + (engine.files_path(self.status_dir),):
            try:
                os.remove(f)
            except OSError:
                pass
        self.log.delete("1.0", "end")
        self._log_pos = 0
        self.running = True
        self.btn.config(text="Stop")
        self.v_status.set(f"Downloading {len(jobs)} link(s)…")
        if bad:
            self._append("Skipped (not supported): " + ", ".join(bad[:5]) + "\n")
        threading.Thread(target=self._run, args=(spec,), daemon=True).start()
        self.root.after(500, self._poll)

    def _run(self, spec):
        try:
            engine.run_queue(spec)
        except Exception as e:
            self._append(f"\nError: {e}\n")

    def _poll(self):
        _, log_path, _ = engine.paths(self.status_dir)
        try:
            with open(log_path, encoding="utf-8") as f:
                f.seek(getattr(self, "_log_pos", 0))
                chunk = f.read()
                self._log_pos = f.tell()
            if chunk:
                self._append(chunk)
        except OSError:
            pass
        st = engine.read_status(self.status_dir) or {}
        if st.get("status"):
            self.v_status.set(st["status"])
        total, seen = st.get("total") or 0, st.get("seen") or 0
        self.prog["value"] = min(seen / total, 1) if total else 0
        state = st.get("state")
        if state in ("done", "error", "cancelled") and not self._engine_running():
            self.running = False
            self.btn.config(text="Download")
            self.prog["value"] = 1 if state == "done" else 0
            return
        self.root.after(500, self._poll)

    def _engine_running(self):
        st = engine.read_status(self.status_dir) or {}
        return st.get("state") == "running" and time.time() - st.get("beat", 0) < 10

    def _append(self, text):
        self.log.insert("end", text)
        self.log.see("end")

    def _copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get("1.0", "end"))
        self.v_status.set("Log copied.")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
