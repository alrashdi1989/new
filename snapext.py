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

        total = sum(len(group["snapList"]) for _, group in sections)
        if not total:
            self.log.info("@%s has no public story, highlights or Spotlight right now", user)
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


def register():
    global _registered
    if not _registered:
        extractor.add(SnapchatFullExtractor)
        _registered = True
