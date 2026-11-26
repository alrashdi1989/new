"""
One global "already downloaded" archive, shared by every download and synced
across devices.

gallery-dl records what it has downloaded in a small SQLite file (table
`archive`, one text `entry` per item, e.g. "snapchat_kocsASE6WQYpTzOML1nHU").
Previously each download folder had its own; here we keep a single file in the
app's data dir so one list covers everything and can be merged with the copy
from the user's other devices through the cloud sync.

No Kivy here, so the app and the background sync service can both use it.
"""

import os
import sqlite3

NAME = "downloaded.sqlite3"
# Keep the synced list within Firestore's 1 MiB per-document limit (alongside
# favorites and logins). ~35 bytes/entry, so 10k entries ≈ 350 KB.
MAX_SYNC = 10000


def path(data_dir):
    return os.path.join(data_dir, NAME)


def _connect(p):
    con = sqlite3.connect(p, timeout=15)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS archive "
                    "(entry TEXT PRIMARY KEY) WITHOUT ROWID")
    except sqlite3.OperationalError:
        con.execute("CREATE TABLE IF NOT EXISTS archive (entry TEXT PRIMARY KEY)")
    return con


def read_entries(data_dir):
    p = path(data_dir)
    if not os.path.exists(p):
        return []
    try:
        con = _connect(p)
        rows = [r[0] for r in con.execute("SELECT entry FROM archive")]
        con.close()
        return rows
    except Exception:
        return []


def merge_entries(data_dir, entries):
    """Add entries (from another device) into the local archive; ignores dups."""
    rows = [(e,) for e in (entries or []) if isinstance(e, str) and e]
    if not rows:
        return 0
    try:
        con = _connect(path(data_dir))
        con.executemany("INSERT OR IGNORE INTO archive (entry) VALUES (?)", rows)
        con.commit()
        n = con.total_changes
        con.close()
        return n
    except Exception:
        return 0


def for_sync(data_dir):
    """The entry list to upload, capped to stay within the document limit."""
    ent = read_entries(data_dir)
    if len(ent) > MAX_SYNC:
        ent = ent[-MAX_SYNC:]
    return ent


def migrate_folders(data_dir, save_dir):
    """One-time: fold any old per-folder archives under save_dir into the global
    one, so switching to a single archive doesn't re-download what's already
    saved. Best effort; safe to call more than once."""
    if not save_dir or not os.path.isdir(save_dir):
        return 0
    found = []
    for root, _dirs, files in os.walk(save_dir):
        if ".downloaded.sqlite3" in files:
            found.append(os.path.join(root, ".downloaded.sqlite3"))
    total = 0
    for fp in found:
        if os.path.abspath(fp) == os.path.abspath(path(data_dir)):
            continue
        try:
            con = sqlite3.connect(fp, timeout=15)
            entries = [r[0] for r in con.execute("SELECT entry FROM archive")]
            con.close()
            total += merge_entries(data_dir, entries)
        except Exception:
            pass
    return total
