"""Final check: downloader + avatar on the rewritten Snapchat path."""
import sys
import requests

import snapext
import avatars
from gallery_dl import extractor, config
from gallery_dl.extractor.message import Message

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

USERS = sys.argv[1:] or ["dr_majoood", "balhaf123", "team.snapchat"]
snapext.register()

for user in USERS:
    print("\n==================  @%s  ==================" % user)
    try:
        config.clear()
        ex = extractor.find("snapfull:" + user)
        ex.initialize()
        urls = [m for m in ex if m[0] == Message.Url]
        vids = sum(1 for m in urls if m[2]["extension"] == "mp4")
        print("  downloader: %d media (%d img / %d vid)"
              % (len(urls), len(urls) - vids, vids))
    except Exception as e:
        print("  downloader ERROR:", repr(e)[:120])

    try:
        url = avatars._snapchat_avatar(user)
        if url:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30, stream=True)
            b = r.raw.read(4096) or b""
            print("  avatar: HTTP %s type=%s bytes=%d"
                  % (r.status_code, r.headers.get("content-type"), len(b)))
        else:
            print("  avatar: none found")
    except Exception as e:
        print("  avatar ERROR:", repr(e)[:120])
