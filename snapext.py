"""
Snapchat add-on for the download engine.

Snapchat's public profile page keeps its data in a Next.js __NEXT_DATA__ JSON
block again (it briefly used React "flight" chunks). This extractor reads that
block and pulls the profile's story, saved highlights and Spotlight videos.

Inside props.pageProps:
  story                current story      -> {"snapList": [...]}  (or null)
  curatedHighlights    saved highlights   -> [{"storyTitle", "snapList": [...]}]
  spotlightHighlights  Spotlight          -> [{"snapList": [...]}]

Each snap in a snapList:
  snapMediaType        0 = image, 1 = video
  snapUrls.mediaUrl    the media to download
  snapId.value         id (may be empty for highlights)
  timestampInSec.value when it was posted (epoch seconds)
  snapTitle.value      optional title

Internal URL form:  snapfull:<username>
"""

import json
import re
from datetime import datetime

from gallery_dl import extractor
from gallery_dl.extractor.common import Message
from gallery_dl.extractor.snapchat import SnapchatExtractor

_NEXT = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def _next_data(html):
    """Return the parsed __NEXT_DATA__ JSON, or None."""
    m = _NEXT.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def _val(x):
    """Snapchat wraps many scalars as {"value": ...}; unwrap them."""
    if isinstance(x, dict):
        return x.get("value")
    return x


def _media_id(src):
    """The CDN's own media key, taken from the download URL itself, e.g.
    https://cf-st.sc-cdn.net/d/kocsASE6WQYpTzOML1nHU.400?mo=...  ->
    "kocsASE6WQYpTzOML1nHU". This is what we use to remember a snap has already
    been downloaded: it is the same no matter how Snapchat restructures the
    page, and it exists for every snap (stories, highlights and Spotlight),
    unlike snapId which is blank for saved highlights. So a future change to the
    page format won't make the app re-download files you already have."""
    seg = src.split("?", 1)[0].rsplit("/", 1)[-1]   # last path part, no query
    return seg.split(".", 1)[0] or seg              # drop size/variant suffix


def _snaps_from(group):
    """Normalize one story/highlight group's snapList into snap dicts."""
    out = []
    if not isinstance(group, dict):
        return out
    gtitle = _val(group.get("storyTitle")) or ""
    for s in group.get("snapList") or []:
        if not isinstance(s, dict):
            continue
        urls = s.get("snapUrls") or {}
        src = urls.get("mediaUrl")
        if not src or not isinstance(src, str):
            continue
        ts = _val(s.get("timestampInSec"))
        try:
            ts = int(ts) if ts else None
        except (ValueError, TypeError):
            ts = None
        out.append({
            "kind": "video" if s.get("snapMediaType") == 1 else "image",
            "id": _media_id(src), "src": src,
            "title": _val(s.get("snapTitle")) or gtitle or "",
            "ts": ts,
        })
    return out


def parse_profile(html):
    """All media on the profile, grouped. Returns a dict:
    {"story": [snap, ...], "spotlight": [snap, ...]} where each snap is
    {kind, id, src, title, ts}."""
    groups = {"story": [], "spotlight": []}
    data = _next_data(html)
    if not data:
        return groups
    pp = ((data.get("props") or {}).get("pageProps")) or {}

    # Story section = current story + saved highlights (Snapchat shows both
    # under Stories now).
    groups["story"] += _snaps_from(pp.get("story"))
    for g in pp.get("curatedHighlights") or []:
        groups["story"] += _snaps_from(g)
    for g in pp.get("spotlightHighlights") or []:
        groups["spotlight"] += _snaps_from(g)

    for name, lst in groups.items():             # drop duplicates within a section
        seen, uniq = set(), []
        for s in lst:
            if s["id"] in seen:
                continue
            seen.add(s["id"])
            uniq.append(s)
        groups[name] = uniq
    return groups


class SnapchatFullExtractor(SnapchatExtractor):
    subcategory = "full"
    pattern = r"snapfull:([^/?#\s]+)"
    example = "snapfull:username"
    archive_fmt = "{file_id}"

    def items(self):
        user = self.groups[0]
        html = self.request(f"{self.root}/@{user}").text
        groups = parse_profile(html)

        wanted = self.config("sections")
        if wanted:
            if isinstance(wanted, str):
                wanted = [w.strip() for w in wanted.split(",")]
            wanted = {w.lower() for w in wanted}
        else:
            wanted = {"story", "highlights", "spotlight"}
        story_on = bool(wanted & {"story", "stories", "highlights"})
        spot_on = "spotlight" in wanted

        sections = []
        if story_on and groups["story"]:
            sections.append(("Story", groups["story"]))
        if spot_on and groups["spotlight"]:
            sections.append(("Spotlight", groups["spotlight"]))

        total = sum(len(s) for _, s in sections)
        if not total:
            if wanted <= {"none", ""}:
                self.log.info("Nothing selected for Snapchat (pick Stories, Highlights "
                              "or Spotlight)")
            else:
                self.log.info("@%s has nothing public in the selected sections right now", user)
            yield Message.Directory, "", {"user": user, "category": "snapchat"}
            return

        num = 0
        for section, snaps in sections:
            yield Message.Directory, "", {"user": user, "section": section,
                                          "category": "snapchat"}
            for snap in snaps:
                num += 1
                is_video = snap["kind"] == "video"
                date = None
                if snap.get("ts"):
                    try:
                        date = datetime.utcfromtimestamp(snap["ts"])
                    except (ValueError, OSError, TypeError):
                        date = None
                data = {
                    "user": user, "section": section, "category": "snapchat",
                    "subcategory": "full", "id": snap["id"][:16] or str(num),
                    "file_id": snap["id"], "num": num, "title": snap["title"],
                    "date": date, "_total": total,
                    "extension": "mp4" if is_video else "jpg",
                }
                yield Message.Url, snap["src"], data


_registered = False


def _patch_tiktok_ytdl_fallback():
    """If listing a TikTok profile through yt-dlp fails, fall back to the
    engine's own method instead of stopping the download."""
    from gallery_dl.extractor import tiktok
    original = tiktok.TiktokPostsExtractor._extract_posts_ytdl

    def safe(self, profile_url):
        try:
            return original(self, profile_url)
        except BaseException as exc:          # yt-dlp raises DownloadError / SystemExit
            if isinstance(exc, KeyboardInterrupt):
                raise
            self.log.warning("yt-dlp couldn't list this profile (%s); trying another way",
                             str(exc).splitlines()[0][:160])
            return []

    tiktok.TiktokPostsExtractor._extract_posts_ytdl = safe


FB_NO_PHOTOS = "Facebook didn't show this profile's photos"


def _patch_facebook_profile():
    """When Facebook's profile page has no photo album (logged out, private
    photos, or a layout the engine can't read), explain it instead of crashing
    with KeyError: 'set_id'."""
    from gallery_dl.extractor import facebook
    original = facebook.FacebookExtractor._extract_profile_page

    def safe(self, url):
        user = original(self, url) or {}
        if not user.get("set_id"):
            self.log.warning("%s (log in to Facebook in the app, or the photos "
                             "aren't public)", FB_NO_PHOTOS)
            user = dict(user)
            user["set_id"] = ""
        return user

    facebook.FacebookExtractor._extract_profile_page = safe


def register():
    global _registered
    if not _registered:
        extractor.add(SnapchatFullExtractor)
        for patch in (_patch_tiktok_ytdl_fallback, _patch_facebook_profile):
            try:
                patch()
            except Exception:
                pass
        _registered = True
