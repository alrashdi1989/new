"""
Snapchat add-on for the download engine.

Snapchat's website moved to Next.js' App Router: the profile page no longer
embeds a __NEXT_DATA__ JSON block (which gallery-dl's own extractor needs), so
the story/highlight/spotlight data now arrives as React "flight" chunks
(self.__next_f.push([1,"..."])). This extractor reconstructs those chunks and
reads the public profile's stories, highlights and Spotlight videos from them.

Each piece of content is a {"playback":{...},"tile":{...}} object:
  playback.kind         "story" (stories + saved highlights) or "spotlight"
  playback.title        the tile's title
  playback.dateMs       when it was posted (epoch ms)
  playback.sequence.items[]  each: kind ("image"/"video"), id, src (media URL)

Internal URL form:  snapfull:<username>
"""

import json
import re
from datetime import datetime, timezone

from gallery_dl import extractor
from gallery_dl.extractor.common import Message
from gallery_dl.extractor.snapchat import SnapchatExtractor

_FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)')


def _flight_text(html):
    """Rebuild the React flight stream from the page's __next_f chunks."""
    out = []
    for c in _FLIGHT.findall(html):
        try:
            out.append(json.loads('"' + c + '"'))
        except ValueError:
            pass
    return "".join(out)


def _balanced(s, start):
    """Return the JSON object that starts at s[start] == '{' (brace-matched)."""
    depth = 0
    instr = esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            instr = not instr
        elif not instr:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i + 1]
    return None


def parse_profile(html):
    """All media on the profile, grouped. Returns a dict:
    {"story": [snap, ...], "spotlight": [snap, ...]} where each snap is
    {kind, id, src, title, dateMs}."""
    flight = _flight_text(html)
    groups = {"story": [], "spotlight": []}
    seen = set()
    for m in re.finditer(r'\{"playback":', flight):
        obj = _balanced(flight, m.start())
        if not obj:
            continue
        try:
            d = json.loads(obj)
        except ValueError:
            continue
        pb = d.get("playback")
        if not isinstance(pb, dict):
            continue
        kind = pb.get("kind")
        if kind not in groups:
            continue
        seq = pb.get("sequence")
        items = seq.get("items", []) if isinstance(seq, dict) else []
        for it in items:
            if not isinstance(it, dict):
                continue
            src, iid = it.get("src"), it.get("id")
            if not src or not isinstance(src, str) or iid in seen:
                continue
            seen.add(iid)
            groups[kind].append({
                "kind": it.get("kind"), "id": iid or "", "src": src,
                "title": pb.get("title") or "", "dateMs": pb.get("dateMs"),
            })
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

        # Snapchat merged current stories and saved highlights into one "story"
        # kind, so Stories and Highlights now draw from the same set.
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
                if snap.get("dateMs"):
                    try:
                        date = datetime.fromtimestamp(snap["dateMs"] / 1000, timezone.utc)
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
