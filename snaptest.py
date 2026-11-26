"""Map the __NEXT_DATA__ JSON shape for the Snapchat parser rewrite."""
import json
import re
import sys
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

USERS = sys.argv[1:] or ["dr_majoood", "balhaf123"]


def grab(html):
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  html, re.S)
    return json.loads(m.group(1)) if m else None


def walk_keys(o, prefix="", depth=0, out=None):
    if out is None:
        out = []
    if depth > 6:
        return out
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("story", "curatedHighlights", "spotlightHighlights",
                     "spotlight", "publicProfileInfo", "snapList", "snapUrls",
                     "props", "pageProps"):
                out.append(prefix + "/" + k + "  (" + type(v).__name__ + ")")
            walk_keys(v, prefix + "/" + k, depth + 1, out)
    elif isinstance(o, list) and o:
        walk_keys(o[0], prefix + "[0]", depth + 1, out)
    return out


for user in USERS:
    print("\n==================  @%s  ==================" % user)
    html = requests.get("https://www.snapchat.com/@" + user,
                        headers={"User-Agent": UA,
                                 "Accept-Language": "en-US,en;q=0.9"},
                        timeout=30).text
    data = grab(html)
    if not data:
        print("  no __NEXT_DATA__")
        continue
    for line in walk_keys(data):
        print("  ", line)
    try:
        pp = data["props"]["pageProps"]
        print("  pageProps keys:", list(pp.keys()))
        info = pp.get("publicProfileInfo") or pp.get("userProfile") or {}
        if isinstance(info, dict):
            print("  profile-info keys:", list(info.keys())[:30])
        # find a snapList anywhere and dump the first snap
        found = []

        def hunt(o):
            if isinstance(o, dict):
                if "snapList" in o and isinstance(o["snapList"], list) and o["snapList"]:
                    found.append((o.get("storyType"), o.get("storyTitle"),
                                  o["snapList"][0]))
                for v in o.values():
                    hunt(v)
            elif isinstance(o, list):
                for v in o:
                    hunt(v)
        hunt(pp)
        print("  snapList groups found:", len(found))
        if found:
            st, title, snap = found[0]
            print("  first group storyType=%s title=%s" % (st, title))
            print("  SNAP OBJECT:")
            print(json.dumps(snap, indent=1)[:900])
    except Exception as e:
        import traceback
        traceback.print_exc()
