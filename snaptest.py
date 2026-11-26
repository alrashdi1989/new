"""Inspect Snapchat profile page structure to repair the parser."""
import re
import sys
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

USERS = sys.argv[1:] or ["balhaf123", "dr_majoood"]

MARKERS = ['self.__next_f.push', '__NEXT_DATA__', '"playback"', '"spotlight"',
           '"story"', '"sequence"', '"snapList"', '"snaps"', '"mediaUrl"',
           '"snapUrl"', 'sc-cdn.net', 'bolt-gcdn', 'cf-st.sc-cdn',
           '"storyTitle"', '"highlight"', 'application/json', '"@type"',
           '"snapId"', '"timestamp"', 'data-json', '"publicProfileInfo"',
           '"curatedHighlights"', '"spotlightHighlights"', '"savedStory"']

for user in USERS:
    print("\n==================  @%s  ==================" % user)
    try:
        html = requests.get("https://www.snapchat.com/@" + user,
                            headers={"User-Agent": UA,
                                     "Accept-Language": "en-US,en;q=0.9"},
                            timeout=30).text
    except Exception as e:
        print("  FETCH ERROR:", e)
        continue
    print("  bytes:", len(html))
    for m in MARKERS:
        c = html.count(m)
        if c:
            print("    %-26s x%d" % (m, c))
    # find media-looking URLs
    media = re.findall(r'https://[^"\\]+\.(?:mp4|jpg|jpeg|webp)[^"\\]*', html)
    print("  media-ish urls:", len(media))
    for u in media[:3]:
        print("     ", u[:90])
    # show a window around the first 'playback' or 'story' or 'snap'
    for key in ('"playback"', '"snapList"', '"story"', '"spotlight"', '"snaps"'):
        i = html.find(key)
        if i != -1:
            print("  context around %s:" % key)
            print("   ", html[i-40:i+220].replace("\n", " "))
            break
    # list all <script> types / ld+json blocks
    scripts = re.findall(r'<script[^>]*type="application/(?:ld\+)?json"[^>]*>(.{0,120})',
                         html)
    print("  json script blocks:", len(scripts))
    for s in scripts[:2]:
        print("     ", s[:110].replace("\n", " "))
