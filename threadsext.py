"""
Threads add-on for the download engine.

Neither gallery-dl nor yt-dlp has a Threads extractor, so this one reads the
public web page (the same data a browser gets) and pulls the photos and videos
out of the JSON that Threads embeds in the page. Threads runs on Instagram's
backend, so each post carries the same media fields as Instagram:
image_versions2 / video_versions / carousel_media.

Internal URL forms:
  threads:<username>                      -> a public profile's posts
  https://www.threads.com/@user/post/CODE -> a single post (also /t/CODE, /p/CODE)

Only what a normal visitor (or your logged-in account, if cookies are set) can
see is read; nothing here gets around a private or blocked profile.
"""

import json
import re

from gallery_dl.extractor.common import Extractor, Message
from gallery_dl import text

DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

_registered = False


def _best(versions):
    """Pick the largest URL from a list of {url,width,height} entries."""
    best_url, best_w = None, -1
    for v in versions or ():
        if isinstance(v, dict) and v.get("url"):
            w = v.get("width") or 0
            if w > best_w:
                best_url, best_w = v["url"], w
    return best_url


def _item_media(item):
    """(url, is_video) for one media item, video preferred over its cover image."""
    if not isinstance(item, dict):
        return None
    url = _best(item.get("video_versions"))
    if url:
        return url, True
    iv = item.get("image_versions2")
    if isinstance(iv, dict):
        url = _best(iv.get("candidates"))
        if url:
            return url, False
    return None


def post_media(post):
    """All media of a post, in order. Handles single posts and carousels."""
    out = []
    carousel = post.get("carousel_media")
    if isinstance(carousel, list) and carousel:
        for child in carousel:
            m = _item_media(child)
            if m:
                out.append(m)
    else:
        m = _item_media(post)
        if m:
            out.append(m)
    return out


def _is_post(node):
    return (isinstance(node, dict) and node.get("taken_at")
            and ("image_versions2" in node or "video_versions" in node
                 or "carousel_media" in node))


def find_posts(node, found, seen):
    """Walk the embedded JSON and collect every post node, newest layout or not."""
    if isinstance(node, dict):
        if _is_post(node):
            key = str(node.get("pk") or node.get("id") or node.get("code") or id(node))
            if key not in seen:
                seen.add(key)
                found.append(node)
            return                      # don't dive into a post's own media/quoted post
        for v in node.values():
            find_posts(v, found, seen)
    elif isinstance(node, list):
        for v in node:
            find_posts(v, found, seen)


def posts_from_html(html):
    """Every post embedded in a Threads HTML page, de-duplicated and newest-first."""
    found, seen = [], set()
    for block in re.findall(
            r'<script[^>]+type="application/json"[^>]*>(.*?)</script>', html, re.S):
        block = block.strip()
        if "image_versions2" not in block and "video_versions" not in block:
            continue
        try:
            data = json.loads(text.unescape(block) if "\\u" in block else block)
        except ValueError:
            try:
                data = json.loads(block)
            except ValueError:
                continue
        find_posts(data, found, seen)
    found.sort(key=lambda p: p.get("taken_at") or 0, reverse=True)
    return found


class ThreadsExtractor(Extractor):
    category = "threads"
    subcategory = "user"
    directory_fmt = ("threads", "{user}")
    filename_fmt = "{id}_{num}.{extension}"
    archive_fmt = "{id}_{num}"
    root = "https://www.threads.com"
    pattern = (r"(?:threads:([^/?#\s]+))|"
               r"(?:https?://(?:www\.)?threads\.(?:com|net)"
               r"/(@[\w.]+/post/[\w-]+|t/[\w-]+|p/[\w-]+|@?[\w.]+))")
    example = "threads:username"

    def _fetch(self, url):
        headers = {"User-Agent": DESKTOP_UA, "Accept-Language": "en-US,en;q=0.9",
                   "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        return self.request(url, headers=headers, notfound="profile").text

    def items(self):
        user_form, path = self.groups[0], self.groups[1]
        want_code = None
        if user_form:
            page_url = f"{self.root}/@{user_form}"
            user = user_form
            single = False
        elif "/post/" in path or path.startswith(("t/", "p/")):
            page_url = f"{self.root}/{path.lstrip('/')}"
            m = re.match(r"@([\w.]+)/post/([\w-]+)", path)
            user = m.group(1) if m else "threads"
            want_code = m.group(2) if m else None
            single = True
        else:                                   # bare @user or user
            user = path.lstrip("@")
            page_url = f"{self.root}/@{user}"
            single = False

        try:
            html = self._fetch(page_url)
        except Exception as exc:
            self.log.error("Couldn't open Threads (%s). The profile may be private, "
                           "renamed, or Threads is blocking the request.", exc)
            yield Message.Directory, "", {"user": user, "category": "threads"}
            return

        posts = posts_from_html(html)
        if single and posts:
            # prefer the post named in the URL; otherwise the main (newest) one
            match = [p for p in posts if str(p.get("code") or "") == want_code] if want_code else []
            posts = match[:1] or posts[:1]

        if not posts:
            if "login" in html and "barcelona" in html.lower():
                self.log.warning("Threads didn't return any posts for @%s. If the account "
                                 "is private, log in to Threads in the app first.", user)
            else:
                self.log.warning("No downloadable posts found for @%s (it may have none "
                                 "public, or Threads changed its page).", user)
            yield Message.Directory, "", {"user": user, "category": "threads"}
            return

        total = len(posts)
        yield Message.Directory, "", {"user": user, "category": "threads", "_total": total}
        for post in posts:
            pid = str(post.get("code") or post.get("pk") or post.get("id") or "")
            when = post.get("taken_at")
            date = self.parse_timestamp(when) if when else None
            media = post_media(post)
            for num, (url, is_video) in enumerate(media, 1):
                data = {
                    "user": user,
                    "category": "threads",
                    "id": pid,
                    "num": num,
                    "count": len(media),
                    "date": date,
                    "_total": total,
                    "extension": "mp4" if is_video else (text.ext_from_url(url) or "jpg"),
                }
                yield Message.Url, url, data


def register():
    global _registered
    if not _registered:
        from gallery_dl import extractor
        extractor.add(ThreadsExtractor)
        _registered = True
