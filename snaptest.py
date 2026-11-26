"""Check which stable fields each snap type has (for method-proof dedup)."""
import json
import re
import sys
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
USERS = sys.argv[1:] or ["balhaf123", "dr_majoood"]


def nd(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


for user in USERS:
    print("\n===== @%s =====" % user)
    html = requests.get("https://www.snapchat.com/@" + user,
                        headers={"User-Agent": UA}, timeout=30).text
    pp = nd(html)["props"]["pageProps"]
    for label, node in (("story", pp.get("story")),
                        ("curatedHighlights", (pp.get("curatedHighlights") or [None])[0]),
                        ("spotlightHighlights", (pp.get("spotlightHighlights") or [None])[0])):
        if not isinstance(node, dict):
            print("  %-20s : none" % label)
            continue
        snaps = node.get("snapList") or []
        print("  %-20s : %d snaps" % (label, len(snaps)))
        if snaps:
            s = snaps[0]
            print("      snap keys:", list(s.keys()))
            for k in ("snapId", "snapMediaType", "timestampInSec", "snapIndex"):
                print("        %-16s = %s" % (k, json.dumps(s.get(k))[:80]))
            mu = (s.get("snapUrls") or {}).get("mediaUrl", "")
            print("        mediaUrl[:95] =", mu[:95])
