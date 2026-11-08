"""
Link handling shared by the app and the floating bubble.
Pure logic (re / json / os only), no Kivy, so a background service can import it.
"""

import json
import os
import re


RESERVED = {"home", "explore", "search", "notifications", "messages", "i", "settings",
            "compose", "login", "logout", "signup", "tos", "privacy", "hashtag", "intent"}

FB_RESERVED = {"groups", "events", "marketplace", "watch", "reel", "reels", "share", "story.php",
               "login", "login.php", "pages", "gaming", "help", "settings", "photo", "photo.php",
               "media", "profile.php", "people", "hashtag", "search", "friends", "messages"}

def parse_username(text):
    """X username from a profile link, @name or bare name (kept for compatibility)."""
    text = text.strip()
    m = re.match(r"^(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/([^/?#]+)", text, re.I)
    name = m.group(1) if m else text.lstrip("@")
    if re.fullmatch(r"[A-Za-z0-9_]{1,15}", name) and name.lower() not in RESERVED:
        return name
    return None


IG_RESERVED = {"explore", "accounts", "direct", "reels", "stories", "p", "reel", "tv",
               "about", "legal", "developer", "web", "challenge", "emails", "session"}

IMAGE_EXT = ("jpg", "jpeg", "png", "webp")


VIDEO_EXT = ("mp4", "mov", "m4v", "webm")


def resolve_link(text):
    """Return (site, name, url) for an X or TikTok link, or None.

    site is "x", "tiktok", "snapchat" or "instagram"; name is used for the folder and status text.
    """
    t = text.strip().split()[0] if text.strip() else ""
    if not t:
        return None
    if not re.match(r"^https?://", t, re.I) and ("." in t.split("/")[0]):
        t = "https://" + t

    # TikTok short share links (vm./vt.tiktok.com/xxxx, tiktok.com/t/xxxx)
    m = re.match(r"^https?://(?:vm|vt)\.tiktok\.com/(\w+)", t, re.I) or \
        re.match(r"^https?://(?:www\.|m\.)?tiktok\.com/t/(\w+)", t, re.I)
    if m:
        return "tiktok", "shared", t.split("?")[0]
    # TikTok single post
    m = re.match(r"^https?://(?:www\.|m\.)?tiktok\.com/@([\w.-]+)/(?:video|photo)/(\d+)", t, re.I)
    if m:
        return "tiktok", m.group(1), f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}"
    # TikTok profile
    m = re.match(r"^https?://(?:www\.|m\.)?tiktok\.com/@([\w.-]+)/?(?:[?#].*)?$", t, re.I)
    if m:
        return "tiktok", m.group(1), f"https://www.tiktok.com/@{m.group(1)}/posts"

    # Instagram
    ig = r"^https?://(?:www\.|m\.)?instagram\.com/"
    m = re.match(ig + r"(?:[\w.]+/)?(p|reel|reels|tv)/([\w-]+)", t, re.I)
    if m:
        kind = "p" if m.group(1).lower() == "p" else "reel"
        return "instagram", "posts", f"https://www.instagram.com/{kind}/{m.group(2)}/"
    m = re.match(ig + r"stories/highlights/(\d+)", t, re.I)
    if m:
        return "instagram", "highlights", f"https://www.instagram.com/stories/highlights/{m.group(1)}/"
    m = re.match(ig + r"stories/([\w.]+)(?:/(\d+))?", t, re.I)
    if m:
        u = f"https://www.instagram.com/stories/{m.group(1)}/" + (f"{m.group(2)}/" if m.group(2) else "")
        return "instagram", m.group(1), u
    m = re.match(ig + r"([\w.]{1,30})/?(?:[?#].*)?$", t, re.I)
    if m and m.group(1).lower() not in IG_RESERVED:
        return "instagram", m.group(1), f"https://www.instagram.com/{m.group(1)}/"

    # YouTube (videos, shorts, playlists, channels)
    yt = r"^https?://(?:www\.|m\.|music\.)?youtube\.com/"
    m = re.match(r"^https?://youtu\.be/([\w-]{11})", t, re.I) or \
        re.match(yt + r"(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)([\w-]{11})", t, re.I)
    if m and "list=" not in t:
        return "youtube", "videos", f"https://www.youtube.com/watch?v={m.group(1)}"
    m = re.search(r"[?&]list=([\w-]+)", t)
    if m and re.match(yt, t, re.I):
        return "youtube", "playlist", f"https://www.youtube.com/playlist?list={m.group(1)}"
    m = re.match(yt + r"(@[\w.-]+|channel/[\w-]+|c/[\w.-]+|user/[\w.-]+)", t, re.I)
    if m:
        return "youtube", m.group(1).split("/")[-1].lstrip("@"), \
            f"https://www.youtube.com/{m.group(1)}/videos"

    # Facebook
    fb = r"^https?://(?:www\.|m\.|web\.|mbasic\.)?facebook\.com/"
    if re.match(r"^https?://fb\.watch/", t, re.I) or re.match(fb + r"share/", t, re.I):
        return "facebook", "shared", t.split("#")[0]          # resolved when downloading
    m = re.match(fb + r"(?:[\w.]+/)?(?:reel|reels)/(\d+)", t, re.I)
    if m:
        return "facebook", "videos", f"https://www.facebook.com/watch/?v={m.group(1)}"
    m = re.match(fb + r"watch/?\?(?:.*&)?v=(\d+)", t, re.I) or re.match(fb + r"[\w.]+/videos/(?:[^/?#]+/)?(\d+)", t, re.I)
    if m:
        return "facebook", "videos", f"https://www.facebook.com/watch/?v={m.group(1)}"
    if re.match(fb + r"(?:photo/?\?|photo\.php\?)", t, re.I):
        # A photo link that names its album downloads the whole album
        m = re.search(r"[?&]set=([\w.-]+)", t)
        if m:
            return "facebook", "albums", f"https://www.facebook.com/media/set/?set={m.group(1)}"
        return "facebook", "photos", re.sub(r"facebook\.com/photo\.php\?", "facebook.com/photo/?",
                                            re.sub(r"^https?://(?:m|web|mbasic)\.", "https://www.", t.split("#")[0]))
    if re.match(fb + r"media/set/?\?", t, re.I):
        return "facebook", "albums", t.split("#")[0]
    m = re.match(fb + r"profile\.php\?(?:.*&)?id=(\d+)", t, re.I) or \
        re.match(fb + r"people/[^/?#]+/(\d+)", t, re.I)
    if m:
        return "facebook", m.group(1), f"https://www.facebook.com/{m.group(1)}/photos"
    m = re.match(fb + r"([\w.-]{2,80})/?(?:(?:photos|photos_by)/?)?(?:[?#].*)?$", t, re.I)
    if m and m.group(1).lower() not in FB_RESERVED:
        return "facebook", m.group(1), f"https://www.facebook.com/{m.group(1)}/photos"

    # Threads (Meta). Single post, or a whole public profile.
    th = r"^https?://(?:www\.)?threads\.(?:com|net)/"
    m = re.match(th + r"(@[\w.]+)/post/([\w-]+)", t, re.I)
    if m:
        return "threads", m.group(1).lstrip("@"), \
            f"https://www.threads.com/{m.group(1)}/post/{m.group(2)}"
    m = re.match(th + r"(?:t|p)/([\w-]+)", t, re.I)
    if m:
        return "threads", "post", f"https://www.threads.com/t/{m.group(1)}"
    m = re.match(th + r"@([\w.]+)/?(?:[?#].*)?$", t, re.I)
    if m:
        return "threads", m.group(1), "threads:" + m.group(1)

    # Snapchat single highlight / story link -> download just that one
    m = re.match(r"^https?://(?:www\.)?snapchat\.com/(?:@|add/)([\w.-]+)/(?:highlight/)?[^/?#]{20,}", t, re.I)
    if m:
        return "snapchat", m.group(1), t.split("?")[0]
    # Snapchat public profile -> current story + all highlights
    m = re.match(r"^https?://(?:www\.)?snapchat\.com/(?:@|add/)([\w.-]+)/?(?:[?#].*)?$", t, re.I) or \
        re.match(r"^https?://story\.snapchat\.com/(?:s/|@)?([\w.-]+)/?(?:[?#].*)?$", t, re.I)
    if m:
        return "snapchat", m.group(1), "snapfull:" + m.group(1)

    # X single post
    m = re.match(r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/(\w{1,15})/status/(\d+)", t, re.I)
    if m:
        return "x", m.group(1), f"https://x.com/{m.group(1)}/status/{m.group(2)}"
    # X profile, @name or bare name
    user = parse_username(text)
    if user:
        return "x", user, f"https://x.com/{user}/media"
    # Any other website: video via yt-dlp, else images via the main engine
    m = re.match(r"^https?://(?:www\.|m\.)?([^/\s:?#]+\.[^/\s:?#]+)", t, re.I)
    if m:
        return "web", m.group(1).lower(), t
    return None


def parse_links(text):
    """Return ([(site, name, url), ...], bad_tokens) from text with one or more links."""
    tokens = [t.strip("<>()[]\"'.,;") for t in re.split(r"[\s,]+", text or "")]
    tokens = [t for t in tokens if t]
    many = len(tokens) > 1
    found, seen, bad = [], set(), []
    for t in tokens:
        # In a list or share text, only accept things that look like links,
        # so ordinary words aren't mistaken for X usernames.
        if many and not ("." in t or "/" in t or t.startswith("@")):
            continue
        link = resolve_link(t)
        if not link:
            if "." in t or "/" in t:
                bad.append(t)
            continue
        if link[2] not in seen:
            seen.add(link[2])
            found.append(link)
    return found, bad


def build_args(site, url, out_dir, auth_token, images, videos, retweets, site_cookies=None,
               site_ua=None, content=None, rename=False):
    content = set(content) if content is not None else {"stories", "highlights", "posts", "spotlight"}
    exts = (IMAGE_EXT if images else ()) + (VIDEO_EXT if videos else ())
    if site == "facebook" and videos:
        exts += ("m4a",)           # Facebook sends a video's sound as a separate file
    args = [
        "-d", out_dir,
        "--download-archive", os.path.join(out_dir, ".downloaded.sqlite3"),
        # Instagram: "merged" = the version with sound built in (the other kind needs
        # a desktop video tool to join picture and sound, which phones don't have)
        "-o", "videos=merged" if site == "instagram" else "videos=true",
        # Android's shared storage rejects : ? * " < > | \ in file names
        "-o", "path-restrict=windows",
    ]
    if rename:
        # temporary name carrying the post's date; the engine renames it to
        # "<profile> <date time>.<ext>" right after it's saved
        args += ["-o", "filename=@@{date}@@{id}_{num}.{extension}"]
    args += [
        "-o", "path-strip=windows",
        "-o", ('directory=["{section}"]' if site == "snapchat" else
               'directory=["{subcategory!c}"]' if (site == "tiktok" and url.endswith("/posts")
                                                   and "stories" in content) else
               'directory=["{subcategory!c}"]' if site == "instagram" and is_ig_profile(url) else
               "directory=[]"),
        "--filter", f"extension in {exts!r}",
    ]
    if site == "x":
        args += ["-o", f"retweets={'true' if retweets else 'false'}"]
        if auth_token:  # the X login is only ever sent to X
            args += ["-o", 'cookies={"auth_token": "%s"}' % auth_token.strip()]
    elif site == "tiktok":
        args += ["-o", "audio=false", "-o", "covers=false"]
        if url.endswith("/posts"):
            # List profile videos with yt-dlp first (kept up to date with TikTok's
            # site changes); the engine falls back to its own method if that fails.
            args += ["-o", "ytdl=true"]
            if "stories" in content or "posts" not in content:
                inc = [k for k in ("posts", "stories") if k in content]
                args += ["-o", "include=" + (",".join(inc) or "none")]
                url = url[:-len("posts")]          # whole profile, filtered by include
        if site_cookies:  # the TikTok login is only ever sent to TikTok
            args += ["-o", "cookies=" + json.dumps(site_cookies)]
    elif site == "snapchat":
        if url.startswith("snapfull:"):
            sec = [n for k, n in (("stories", "Story"), ("highlights", "Highlights"),
                                  ("spotlight", "Spotlight")) if k in content]
            args += ["-o", "sections=" + (",".join(sec) or "none")]
    elif site == "facebook":
        if site_cookies:  # the Facebook login is only ever sent to Facebook
            args += ["-o", "cookies=" + json.dumps(site_cookies)]
    elif site == "instagram":
        if is_ig_profile(url):
            inc = [k for k in ("stories", "highlights", "posts") if k in content]
            args += ["-o", "include=" + (",".join(inc) or "none")]
        if site_cookies:  # the Instagram login is only ever sent to Instagram
            args += ["-o", "cookies=" + json.dumps(site_cookies)]
    elif site == "threads":
        if site_cookies:  # the Threads login is only ever sent to Threads
            args += ["-o", "cookies=" + json.dumps(site_cookies)]
    # Report each post's id plus the profile's post count, so the app can show
    # "post N of ~total" (only meaningful for whole profiles).
    if site == "x" and url.endswith("/media"):
        args += ["--Print", "@@META {user[media_count]}|{tweet_id}"]
    elif site == "tiktok" and url.endswith("/posts"):
        args += ["--Print", "@@META {authorStats[videoCount]}|{id}"]
    elif site == "instagram" and is_ig_profile(url):
        args += ["--Print", "@@META 0|{post_shortcode|post_id|media_id}"]
    elif site == "snapchat" and url.startswith("snapfull:"):
        args += ["--Print", "@@META {_total}|{id}_{num}_{file_id}"]
    elif site == "threads":
        args += ["--Print", "@@META {_total}|{id}_{num}"]
    args.append(url)
    return args


SITE_NAMES = {"x": "X", "tiktok": "TikTok", "snapchat": "Snapchat", "instagram": "Instagram",
              "facebook": "Facebook", "youtube": "YouTube", "threads": "Threads", "web": "website"}


FOLDERS = {"tiktok": "TikTok", "snapchat": "Snapchat", "instagram": "Instagram",
           "facebook": "Facebook", "youtube": "YouTube", "threads": "Threads", "web": "Web"}


WEB_FORMAT_HINT = ("bv*[ext=mp4][height<=1080]+ba[ext=m4a]/b[ext=mp4][height<=1080]"
                   "/b[height<=1080]/b")


def is_ig_profile(url):
    return re.fullmatch(r"https://www\.instagram\.com/[\w.]+/", url) is not None


def output_dir(save_dir, site, name):
    # X keeps its original layout so earlier downloads aren't repeated.
    if site == "x":
        return os.path.join(save_dir, name)
    if site in ("youtube", "web"):   # sorted into channel/site folders by the downloader
        return os.path.join(save_dir, FOLDERS[site])
    return os.path.join(save_dir, FOLDERS[site], name)


def pretty_path(path):
    for prefix, name in (("/storage/emulated/0/", ""), (os.path.expanduser("~") + "/", "~/")):
        if path.startswith(prefix):
            return name + path[len(prefix):]
    return path
