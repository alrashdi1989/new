"""
Media Downloader — desktop (Windows) version.

Same download engine as the phone app (engine.py, links.py, ytjob.py,
storyjoin.py, snapext.py, threadsext.py), with a modern glassy window built
with CustomTkinter (plus a Windows 11 acrylic backdrop when available). On a PC
it uses ffmpeg directly and can reuse the logins from your web browser.
"""

import json
import os
import sys
import threading
import time

import customtkinter as ctk
from tkinter import filedialog, messagebox, StringVar, BooleanVar, Listbox

import engine
import links as L

APP_VERSION = "1.2"

# ---- palette (glassy dark) ------------------------------------------------
BG = "#0d1024"
CARD = "#191d3a"
CARD2 = "#222747"
FG = "#eef1ff"
MUT = "#9aa0c8"
ACC = "#4fd0ff"
ACC_DK = "#073042"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


# ---- where ffmpeg and our config live -------------------------------------

def _bundle_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _ensure_ffmpeg_on_path():
    d = _bundle_dir()
    exe = os.path.join(d, "ffmpeg.exe")
    if os.path.exists(exe):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        return exe
    return "ffmpeg"


FFMPEG = _ensure_ffmpeg_on_path()


def config_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "MediaDownloader")
    os.makedirs(d, exist_ok=True)
    return d


def _app_path():
    """The launched .exe (when frozen) or this script."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(__file__)


def make_shortcuts(desktop=True, start_menu=True):
    """Create Desktop and Start-menu shortcuts to this app. Windows only.
    Returns (ok, message)."""
    if sys.platform != "win32":
        return False, "Shortcuts are only created on Windows."
    import subprocess
    target = _app_path()
    workdir = os.path.dirname(target)
    places = []
    if desktop:
        places.append('[Environment]::GetFolderPath("Desktop")')
    if start_menu:
        places.append('([Environment]::GetFolderPath("StartMenu") + "\\Programs")')
    ps = (
        "$W = New-Object -ComObject WScript.Shell; "
        f"$places = @({','.join(places)}); "
        "foreach ($d in $places) { "
        "  $lnk = Join-Path $d 'Media Downloader.lnk'; "
        "  $s = $W.CreateShortcut($lnk); "
        f"  $s.TargetPath = '{target}'; "
        f"  $s.IconLocation = '{target},0'; "
        f"  $s.WorkingDirectory = '{workdir}'; "
        "  $s.Description = 'Media Downloader'; "
        "  $s.Save() }"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       creationflags=0x08000000, timeout=20, check=True)
        return True, "Shortcut added to your Desktop and Start menu."
    except Exception as e:
        return False, f"Couldn't create the shortcut: {e}"


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
        if browser:
            args += ["--cookies-from-browser", browser]
        jobs.append({"args": args, "out": out, "out_label": out, "site": site, "user": user,
                     "rename": opt["rename"],
                     "join_stories": "join" in content and "stories" in content,
                     "story_only_combined": opt.get("only_combined", False)})
    for i, j in enumerate(jobs, 1):
        j["pos"] = [i, len(jobs)]
    return jobs


# ---- the window -----------------------------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        self.s = load_settings()
        self.status_dir = os.path.join(config_dir(), "job")
        os.makedirs(self.status_dir, exist_ok=True)
        self.running = False
        self._vars()
        self._build_ui()
        self._restore()
        self._try_acrylic()
        self._first_run_shortcuts()

    def _first_run_shortcuts(self):
        """On the very first run, add a desktop + Start-menu shortcut automatically."""
        if sys.platform == "win32" and not self.s.get("shortcuts_made"):
            ok, _msg = make_shortcuts()
            if ok:
                self.s["shortcuts_made"] = True
                save_settings(self.s)
                self.v_status.set("Added a shortcut to your Desktop and Start menu. "
                                  "To pin it to the taskbar, right-click the icon → Pin to taskbar.")

    def _add_shortcut(self):
        ok, msg = make_shortcuts()
        self.v_status.set(msg)
        if ok:
            messagebox.showinfo(
                "Shortcut added",
                "A 'Media Downloader' shortcut is now on your Desktop and in the Start menu.\n\n"
                "To pin it to the taskbar (Windows doesn't let apps do this automatically):\n"
                "• While the app is open, right-click its taskbar icon → Pin to taskbar, or\n"
                "• Right-click the desktop shortcut → Show more options → Pin to taskbar.")

    def _try_acrylic(self):
        """Windows 11: put a real frosted-glass backdrop behind the window."""
        try:
            import pywinstyles
            pywinstyles.apply_style(self.root, "acrylic")
        except Exception:
            pass

    def _vars(self):
        self.v_photos = BooleanVar(value=True)
        self.v_videos = BooleanVar(value=True)
        self.v_reposts = BooleanVar(value=False)
        self.v_story = BooleanVar(value=True)
        self.v_high = BooleanVar(value=True)
        self.v_posts = BooleanVar(value=True)
        self.v_spot = BooleanVar(value=True)
        self.v_join = BooleanVar(value=False)
        self.v_only = BooleanVar(value=False)
        self.v_rename = BooleanVar(value=True)
        self.v_folder = StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads",
                                                     "MediaDownloader"))
        self.v_browser = StringVar(value=BROWSERS[0])
        self.v_status = StringVar(value="Ready.")

    # ---------- UI
    def _card(self, parent, **kw):
        return ctk.CTkFrame(parent, fg_color=CARD, corner_radius=18,
                            border_width=1, border_color="#2c315a", **kw)

    def _title(self, parent, text, size=13, color=MUT, bold=False):
        return ctk.CTkLabel(parent, text=text, text_color=color, anchor="w",
                            font=ctk.CTkFont(size=size, weight="bold" if bold else "normal"))

    def _check(self, parent, text, var):
        return ctk.CTkCheckBox(parent, text=text, variable=var, command=self._save,
                               checkbox_width=20, checkbox_height=20, corner_radius=6,
                               fg_color=ACC, hover_color=ACC, text_color=FG,
                               font=ctk.CTkFont(size=13))

    def _build_ui(self):
        r = self.root
        r.title(f"Media Downloader {APP_VERSION}")
        r.geometry("780x820")
        r.minsize(640, 680)
        r.configure(fg_color=BG)

        header = ctk.CTkFrame(r, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(16, 6))
        ctk.CTkLabel(header, text="Media Downloader",
                     font=ctk.CTkFont(size=22, weight="bold"), text_color=FG).pack(side="left")
        ctk.CTkLabel(header, text=f"v{APP_VERSION}", text_color=MUT,
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=8, pady=(8, 0))
        ctk.CTkButton(header, text="★  Add desktop shortcut", width=170, command=self._add_shortcut,
                      fg_color=CARD, hover_color=CARD2, text_color=FG,
                      corner_radius=10).pack(side="right", pady=(4, 0))

        self.tabs = ctk.CTkTabview(r, fg_color=BG, segmented_button_fg_color=CARD,
                                   segmented_button_selected_color=ACC,
                                   segmented_button_selected_hover_color=ACC,
                                   text_color=FG, corner_radius=16)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        self.tabs.add("Download")
        self.tabs.add("Favorites")
        self._build_download(self.tabs.tab("Download"))
        self._build_favorites(self.tabs.tab("Favorites"))

        self.v_status_lbl = ctk.CTkLabel(r, textvariable=self.v_status, text_color=ACC,
                                         anchor="w", font=ctk.CTkFont(size=12))
        self.v_status_lbl.pack(fill="x", padx=24, pady=(0, 10))

    def _build_download(self, p):
        p.configure(fg_color="transparent")
        scroll = ctk.CTkScrollableFrame(p, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        c1 = self._card(scroll)
        c1.pack(fill="x", pady=(6, 10))
        self._title(c1, "Paste links  —  X, TikTok, Snapchat, Instagram, Threads, Facebook, "
                        "YouTube, or any website", bold=True).pack(fill="x", padx=16, pady=(14, 4))
        self.links = ctk.CTkTextbox(c1, height=90, fg_color=CARD2, text_color=FG,
                                    corner_radius=12, font=ctk.CTkFont(size=13))
        self.links.pack(fill="x", padx=16, pady=(0, 10))

        grid = ctk.CTkFrame(c1, fg_color="transparent")
        grid.pack(fill="x", padx=12, pady=(0, 6))
        checks = [self._check(grid, t, v) for t, v in
                  [("Photos", self.v_photos), ("Videos", self.v_videos),
                   ("X reposts", self.v_reposts), ("Stories", self.v_story),
                   ("Highlights", self.v_high), ("Posts", self.v_posts),
                   ("Spotlight", self.v_spot)]]
        for i, ch in enumerate(checks):
            ch.grid(row=i // 4, column=i % 4, sticky="w", padx=8, pady=5)

        row2 = ctk.CTkFrame(c1, fg_color="transparent")
        row2.pack(fill="x", padx=12, pady=(0, 12))
        self._check(row2, "Stories as one video", self.v_join).pack(side="left", padx=4)
        self._check(row2, "keep only the combined", self.v_only).pack(side="left", padx=10)
        self._check(row2, "Name files profile + time", self.v_rename).pack(side="left", padx=10)

        c2 = self._card(scroll)
        c2.pack(fill="x", pady=(0, 10))
        fr = ctk.CTkFrame(c2, fg_color="transparent")
        fr.pack(fill="x", padx=16, pady=(14, 8))
        self._title(fr, "Save to").pack(anchor="w")
        row = ctk.CTkFrame(fr, fg_color="transparent")
        row.pack(fill="x", pady=4)
        ctk.CTkEntry(row, textvariable=self.v_folder, fg_color=CARD2, text_color=FG,
                     corner_radius=10, border_width=0).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Browse", width=90, command=self._pick_folder,
                      fg_color=CARD2, hover_color="#2c315a", text_color=FG,
                      corner_radius=10).pack(side="left", padx=(8, 0))

        lr = ctk.CTkFrame(c2, fg_color="transparent")
        lr.pack(fill="x", padx=16, pady=(0, 14))
        self._title(lr, "Use logins from browser   (only for private / age-restricted "
                        "accounts)").pack(anchor="w")
        ctk.CTkOptionMenu(lr, variable=self.v_browser, values=BROWSERS, command=lambda *_: self._save(),
                          fg_color=CARD2, button_color=ACC_DK, button_hover_color=ACC,
                          text_color=FG, corner_radius=10, width=180).pack(anchor="w", pady=4)

        btns = ctk.CTkFrame(scroll, fg_color="transparent")
        btns.pack(fill="x", pady=(0, 8))
        self.btn = ctk.CTkButton(btns, text="⬇  Download", command=self._on_download,
                                 fg_color=ACC, hover_color="#38b8e8", text_color=ACC_DK,
                                 font=ctk.CTkFont(size=15, weight="bold"), height=46,
                                 corner_radius=14)
        self.btn.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(btns, text="Clear", width=90, command=lambda: self.links.delete("1.0", "end"),
                      fg_color=CARD, hover_color=CARD2, text_color=FG, height=46,
                      corner_radius=14).pack(side="left", padx=(10, 0))

        self.prog = ctk.CTkProgressBar(scroll, progress_color=ACC, fg_color=CARD, height=8,
                                       corner_radius=4)
        self.prog.set(0)
        self.prog.pack(fill="x", pady=(2, 10))

        c3 = self._card(scroll)
        c3.pack(fill="both", expand=True)
        bar = ctk.CTkFrame(c3, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(12, 4))
        self._title(bar, "Log", bold=True).pack(side="left")
        ctk.CTkButton(bar, text="Copy log", width=90, command=self._copy_log, fg_color=CARD2,
                      hover_color="#2c315a", text_color=FG, corner_radius=10).pack(side="right")
        self.log = ctk.CTkTextbox(c3, height=200, fg_color="#0a0c1c", text_color=MUT,
                                  corner_radius=12, font=ctk.CTkFont(size=12))
        self.log.pack(fill="both", expand=True, padx=16, pady=(0, 14))

    def _build_favorites(self, p):
        p.configure(fg_color="transparent")
        c = self._card(p)
        c.pack(fill="both", expand=True, pady=6)
        self._title(c, "Saved profiles — \"Get new\" downloads anything new since last time",
                    bold=True).pack(fill="x", padx=16, pady=(14, 6))
        self.fav_list = Listbox(c, bg=CARD2, fg=FG, relief="flat", selectmode="extended",
                                highlightthickness=0, borderwidth=0, font=("Segoe UI", 11),
                                selectbackground=ACC_DK, selectforeground=FG, activestyle="none")
        self.fav_list.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        bar = ctk.CTkFrame(c, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkButton(bar, text="Add from links", command=self._fav_add, fg_color=CARD2,
                      hover_color="#2c315a", text_color=FG, corner_radius=10,
                      width=130).pack(side="left")
        ctk.CTkButton(bar, text="Remove", command=self._fav_remove, fg_color=CARD2,
                      hover_color="#2c315a", text_color=FG, corner_radius=10,
                      width=90).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Get new (all)", command=lambda: self._fav_get(False),
                      fg_color=ACC, hover_color="#38b8e8", text_color=ACC_DK, corner_radius=10,
                      font=ctk.CTkFont(weight="bold")).pack(side="right")
        ctk.CTkButton(bar, text="Get new (selected)", command=lambda: self._fav_get(True),
                      fg_color=CARD2, hover_color="#2c315a", text_color=FG,
                      corner_radius=10).pack(side="right", padx=6)

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
        for key, var in (("stories", self.v_story), ("highlights", self.v_high),
                         ("posts", self.v_posts), ("spotlight", self.v_spot),
                         ("join", self.v_join)):
            if var.get():
                c.add(key)
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
        for _site, _user, url in found:
            if url and url not in existing:
                self.fav_list.insert("end", url)
                existing.add(url)
                added += 1
        self._save()
        self.v_status.set(f"Added {added} to favorites." if added else "Nothing new to add.")

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
        self.tabs.set("Download")
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
        for f in engine.paths(self.status_dir) + (engine.files_path(self.status_dir),):
            try:
                os.remove(f)
            except OSError:
                pass
        self.log.delete("1.0", "end")
        self._log_pos = 0
        self.running = True
        self.btn.configure(text="■  Stop")
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
        self.prog.set(min(seen / total, 1) if total else 0)
        state = st.get("state")
        if state in ("done", "error", "cancelled") and not self._engine_running():
            self.running = False
            self.btn.configure(text="⬇  Download")
            self.prog.set(1 if state == "done" else 0)
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
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
