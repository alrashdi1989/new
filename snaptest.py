"""Verify extractor + that archive keys (file_id) are stable across fetches."""
import sys
import snapext
from gallery_dl import extractor, config
from gallery_dl.extractor.message import Message

USERS = sys.argv[1:] or ["dr_majoood", "balhaf123"]
snapext.register()


def keys(user):
    config.clear()
    ex = extractor.find("snapfull:" + user)
    ex.initialize()
    out = []
    for m in ex:
        if m[0] == Message.Url:
            out.append((m[2]["file_id"], m[2]["extension"], m[2]["section"]))
    return out


for user in USERS:
    print("\n===== @%s =====" % user)
    a = keys(user)
    b = keys(user)                      # fetch again -> keys must be identical
    print("  media:", len(a))
    print("  sample file_ids:", [k[0] for k in a[:3]])
    sa, sb = {k[0] for k in a}, {k[0] for k in b}
    print("  stable across two fetches:", sa == sb, "(%d keys both times)" % len(sa))
    # keys must be non-empty and look like CDN ids (alnum), not md5/url
    bad = [k[0] for k in a if not k[0] or len(k[0]) < 8]
    print("  empty/short keys:", len(bad))
    # uniqueness
    print("  unique keys:", len(sa), "of", len(a))
