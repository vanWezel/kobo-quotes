#!/usr/bin/env python3
"""
Export highlights/annotations from a Kobo e-reader into a JSON file of quotes,
suitable for a Scriptable iOS widget to read (e.g. via iCloud Drive).

Usage:
    python3 export_kobo_highlights.py
    python3 export_kobo_highlights.py --db /Volumes/KOBOeReader/.kobo/KoboReader.sqlite
    python3 export_kobo_highlights.py --out ~/Library/Mobile\ Documents/com~apple~CloudDocs/KoboQuotes/quotes.json

Re-running is safe: existing quotes (matched by BookmarkID) are kept as-is,
new ones are appended, and quotes removed from the Kobo are left in the
output untouched (deletions on-device don't delete history here).

Books are gated by a whitelist (book_whitelist.json next to this script by
default). The first time a book's highlights show up, you're prompted to
approve or reject it; the decision is remembered so you're never asked again
for that book. To change your mind later, edit book_whitelist.json directly
(flip "decision" to "approved" or "rejected") and re-run.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

# Scriptable's own iCloud container - the widget script (Kobo Quotes.js) reads
# straight from here, so no cross-app file bookmarking is needed. This folder
# only exists once Scriptable has been installed and opened at least once.
DEFAULT_OUT = (
    Path.home()
    / "Library/Mobile Documents/iCloud~dk~simonbs~Scriptable/Documents/quotes.json"
)

DEFAULT_WHITELIST = Path(__file__).parent / "book_whitelist.json"


def find_kobo_db() -> Path | None:
    volumes = Path("/Volumes")
    if not volumes.exists():
        return None
    for volume in volumes.iterdir():
        candidate = volume / ".kobo" / "KoboReader.sqlite"
        if candidate.exists():
            return candidate
    return None


def open_readonly(db_path: Path, retries: int = 5, delay: float = 1.0) -> sqlite3.Connection:
    # Open with the SQLite URI 'ro' mode so we never write to (or lock)
    # the live database the Kobo itself uses.
    uri = f"file:{db_path}?mode=ro"
    # Right after the Kobo mounts, the volume can be briefly unready and
    # sqlite3 fails with "unable to open database file" even though the
    # file exists; retrying a few times clears it up.
    for attempt in range(retries):
        try:
            return sqlite3.connect(uri, uri=True)
        except sqlite3.OperationalError:
            if attempt == retries - 1:
                raise
            time.sleep(delay)


def fetch_highlights(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(Bookmark)")
    columns = {row[1] for row in cur.fetchall()}

    select_cols = ["BookmarkID", "Text", "Annotation", "DateCreated", "VolumeID"]
    missing = [c for c in select_cols if c not in columns]
    if missing:
        sys.exit(f"Bookmark table is missing expected columns: {missing}. "
                  f"Kobo firmware may have changed its schema.")

    query = """
        SELECT
            Bookmark.BookmarkID,
            Bookmark.Text,
            Bookmark.Annotation,
            Bookmark.DateCreated,
            Bookmark.VolumeID,
            content.Title,
            content.Attribution
        FROM Bookmark
        LEFT JOIN content ON Bookmark.VolumeID = content.ContentID
        WHERE Bookmark.Text IS NOT NULL AND TRIM(Bookmark.Text) != ''
        ORDER BY Bookmark.DateCreated
    """
    cur.execute(query)

    quotes = []
    for bookmark_id, text, annotation, date_created, volume_id, title, author in cur.fetchall():
        quotes.append({
            "id": bookmark_id,
            "text": text.strip(),
            "note": annotation.strip() if annotation else None,
            "book": title or "Unknown",
            "author": author or None,
            "date": date_created,
            "volume_id": volume_id,  # internal only; stripped before writing output
        })
    return quotes


def load_whitelist(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_whitelist(path: Path, whitelist: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(whitelist, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_whitelist(fresh: list[dict], whitelist: dict) -> None:
    """Prompt for any book not yet decided. Mutates `whitelist` in place so
    partial progress is kept even if the user bails out partway through."""
    books = {}
    order = []
    for q in fresh:
        vid = q["volume_id"]
        if vid not in books:
            books[vid] = {"title": q["book"], "author": q["author"], "count": 0}
            order.append(vid)
        books[vid]["count"] += 1

    for vid in order:
        if vid in whitelist:
            continue
        info = books[vid]
        author = f" by {info['author']}" if info["author"] else ""
        n = info["count"]
        prompt = (
            f"\nNew book: \"{info['title']}\"{author} ({n} highlight{'s' if n != 1 else ''})\n"
            f"Include its highlights in the export? [y/N/q] "
        )
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nStopping; undecided books will be asked again next run.")
            break
        if answer == "q":
            print("Stopping; remaining new books will be asked again next run.")
            break
        whitelist[vid] = {
            "title": info["title"],
            "author": info["author"],
            "decision": "approved" if answer == "y" else "rejected",
        }


def merge(existing: list[dict], fresh: list[dict]) -> list[dict]:
    by_id = {q["id"]: q for q in existing}
    for q in fresh:
        by_id[q["id"]] = q  # fresh data wins if a highlight was edited on-device
    return sorted(by_id.values(), key=lambda q: q["date"] or "")


def write_atomic(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)  # atomic on the same filesystem


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="Path to KoboReader.sqlite "
                         "(default: auto-detect under /Volumes)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                         help=f"Output JSON path (default: {DEFAULT_OUT})")
    parser.add_argument("--whitelist", type=Path, default=DEFAULT_WHITELIST,
                         help=f"Book whitelist JSON path (default: {DEFAULT_WHITELIST})")
    args = parser.parse_args()

    db_path = args.db or find_kobo_db()
    if db_path is None:
        sys.exit("Could not find KoboReader.sqlite. Plug in your Kobo via USB, "
                  "or pass --db explicitly.")
    if not db_path.exists():
        sys.exit(f"No such file: {db_path}")

    # sqlite3 needs a real path (not a Path object) inside the URI.
    conn = open_readonly(db_path)
    try:
        fresh = fetch_highlights(conn)
    finally:
        conn.close()

    whitelist = load_whitelist(args.whitelist)
    resolve_whitelist(fresh, whitelist)
    save_whitelist(args.whitelist, whitelist)

    approved_ids = {vid for vid, info in whitelist.items() if info["decision"] == "approved"}
    fresh_approved = [
        {k: v for k, v in q.items() if k != "volume_id"}
        for q in fresh if q["volume_id"] in approved_ids
    ]

    existing = []
    if args.out.exists():
        existing = json.loads(args.out.read_text(encoding="utf-8"))

    combined = merge(existing, fresh_approved)
    write_atomic(args.out, combined)

    pending = {q["volume_id"] for q in fresh} - set(whitelist)
    print(f"Read {len(fresh)} highlights from {db_path}")
    print(f"Included {len(fresh_approved)} highlights from approved books")
    if pending:
        print(f"{len(pending)} book(s) still undecided; you'll be asked again next run")
    print(f"Wrote {len(combined)} total quotes to {args.out}")


if __name__ == "__main__":
    main()
