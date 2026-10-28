"""
Snapchat add-on for the download engine.

gallery-dl's own Snapchat profile support skips the current (24-hour) story.
This extractor reads a public profile once and yields the current story,
all saved highlights, and the Spotlight videos listed on the profile. Internal URL form:  snapfull:<username>
"""

from gallery_dl import extractor
from gallery_dl.extractor.common import Message
from gallery_dl.extractor.snapchat import SnapchatExtractor


class SnapchatFullExtractor(SnapchatExtractor):
    subcategory = "full"
    pattern = r"snapfull:([^/?#\s]+)"
    example = "snapfull:username"

    def items(self):
        user = self.groups[0]
        page = self._request_next_data(f"{self.root}/@{user}")
        page["user"] = user

        sections = []
        story = page.get("story") or {}
        if story.get("snapList"):
            sections.append(("Story", story))
        for highlight in page.get("curatedHighlights") or ():
            if highlight.get("snapList"):
                sections.append(("Highlights", highlight))
        # Only the creator's own Spotlights listed on the profile page.
        # (A Spotlight page's feed is mostly other people's videos, so it's not used.)
        for spot in page.get("spotlightHighlights") or ():
            if spot.get("snapList"):
                sections.append(("Spotlight", spot))

        wanted = self.config("sections")
        if wanted:
            if isinstance(wanted, str):
                wanted = [w.strip() for w in wanted.split(",")]
            wanted = {w.lower() for w in wanted}
            sections = [(n, g) for n, g in sections if n.lower() in wanted]

        total = sum(len(group["snapList"]) for _, group in sections)
        if not total:
            if wanted and wanted <= {"none", ""}:
                self.log.info("Nothing selected for Snapchat (pick Stories, Highlights or Spotlight)")
            else:
                self.log.info("@%s has nothing public in the selected sections right now", user)
            yield Message.Directory, "", page

        current = None
        for section, group in sections:
            if section != current:
                current = section
                yield Message.Directory, "", {"user": user, "section": section,
                                              "category": "snapchat"}
            for snap in group["snapList"]:
                try:
                    if section == "Spotlight":
                        url, data = self._extract_spotlight_item(dict(group), snap)
                    else:
                        url, data = self._extract_story_item(dict(group), snap)
                except Exception as exc:
                    self.log.error("Skipped one snap (%s: %s)",
                                   exc.__class__.__name__, exc)
                    continue
                data["user"] = user
                data["section"] = section
                preview = (snap.get("snapUrls") or {}).get("mediaPreviewUrl")
                if preview and preview != url:
                    data["_fallback"] = (preview,)
                data["_total"] = total
                yield Message.Url, url, data


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
