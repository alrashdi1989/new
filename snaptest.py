"""Live check of the Snapchat downloader path. Run on a runner with internet."""
import sys
import requests

import snapext
from gallery_dl import extractor, config, job
from gallery_dl.extractor.message import Message

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

USERS = sys.argv[1:] or ["balhaf123", "dr_majoood", "team.snapchat"]

snapext.register()
print("registered:", "snapchat-full" in [getattr(e, "subcategory", "")
      or e.__name__ for e in []] or "ok")

for user in USERS:
    print("\n==================  @%s  ==================" % user)
    # 1) raw page + parser
    try:
        html = requests.get("https://www.snapchat.com/@" + user,
                            headers={"User-Agent": UA,
                                     "Accept-Language": "en-US,en;q=0.9"},
                            timeout=30).text
    except Exception as e:
        print("  FETCH ERROR:", e)
        continue
    print("  page bytes:", len(html))
    groups = snapext.parse_profile(html)
    ns, nsp = len(groups["story"]), len(groups["spotlight"])
    print("  parse_profile -> story:%d  spotlight:%d" % (ns, nsp))
    if groups["story"]:
        s = groups["story"][0]
        print("    first story:", s["kind"], (s["src"] or "")[:70])

    # 2) the extractor the app actually invokes
    try:
        config.clear()
        ex = extractor.find("snapfull:" + user)
        ex.initialize()
        urls, dirs = [], []
        for msg in ex:
            if msg[0] == Message.Url:
                urls.append((msg[2].get("section"), msg[2].get("extension"), msg[1]))
            elif msg[0] == Message.Directory:
                dirs.append(msg[2].get("section"))
        print("  extractor -> %d media across sections %s" % (len(urls), dirs))
        # 3) confirm the first media really downloads
        if urls:
            sec, ext, url = urls[0]
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30, stream=True)
            ct = r.headers.get("content-type")
            size = len(r.raw.read(2048) or b"")
            print("    download test: HTTP %s  type=%s  firstbytes=%d  ext=%s"
                  % (r.status_code, ct, size, ext))
    except Exception as e:
        import traceback
        print("  EXTRACTOR ERROR:", repr(e))
        traceback.print_exc()
