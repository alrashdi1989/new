"""Verify the rewritten Snapchat parser end-to-end + find the avatar field."""
import json
import re
import sys
import requests

import snapext
from gallery_dl import extractor, config
from gallery_dl.extractor.message import Message

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

USERS = sys.argv[1:] or ["dr_majoood", "balhaf123"]
snapext.register()


def get(user):
    return requests.get("https://www.snapchat.com/@" + user,
                        headers={"User-Agent": UA,
                                 "Accept-Language": "en-US,en;q=0.9"},
                        timeout=30).text


for user in USERS:
    print("\n==================  @%s  ==================" % user)
    html = get(user)

    g = snapext.parse_profile(html)
    print("  parse_profile -> story:%d  spotlight:%d" % (len(g["story"]), len(g["spotlight"])))

    config.clear()
    ex = extractor.find("snapfull:" + user)
    ex.initialize()
    urls, dirs = [], []
    for msg in ex:
        if msg[0] == Message.Url:
            urls.append((msg[2]["section"], msg[2]["extension"],
                         msg[2]["num"], msg[1]))
        elif msg[0] == Message.Directory:
            if msg[2].get("section"):
                dirs.append(msg[2]["section"])
    print("  extractor -> %d media, sections=%s" % (len(urls), dirs))
    vids = sum(1 for u in urls if u[1] == "mp4")
    print("  images=%d videos=%d" % (len(urls) - vids, vids))

    # download test: one image + one video
    for want in ("jpg", "mp4"):
        pick = next((u for u in urls if u[1] == want), None)
        if not pick:
            print("  no %s to test" % want)
            continue
        r = requests.get(pick[3], headers={"User-Agent": UA}, timeout=30, stream=True)
        chunk = r.raw.read(4096) or b""
        print("  download %s: HTTP %s type=%s bytes=%d"
              % (want, r.status_code, r.headers.get("content-type"), len(chunk)))

    # avatar field hunt in __NEXT_DATA__
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    data = json.loads(m.group(1))
    pp = data["props"]["pageProps"]
    info = (pp.get("userProfile") or {}).get("publicProfileInfo") or {}
    print("  publicProfileInfo keys:", list(info.keys()))
    for k in ("bitmoji3d", "bitmojiBackgroundUrl", "profilePictureUrl",
              "snapcodeImageUrl", "imageUrl", "bitmojiSelfie"):
        if k in info:
            v = info[k]
            print("    %s = %s" % (k, (json.dumps(v)[:90])))
    # any url containing 'bitmoji' or 'profile'
    for mm in re.findall(r'https://[^"\\]*(?:bitmoji|profile|snapcode)[^"\\]*', json.dumps(info))[:3]:
        print("    avatar-ish:", mm[:90])
