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
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

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
    try:
        entries = list(volumes.iterdir())
    except PermissionError:
        return None
    for volume in entries:
        try:
            candidate = volume / ".kobo" / "KoboReader.sqlite"
            if candidate.exists():
                return candidate
        except PermissionError:
            # Some mounted volumes (e.g. network shares, Time Machine)
            # refuse traversal; skip rather than aborting the whole scan.
            continue
    return None


def open_readonly(db_path: Path, retries: int = 5, delay: float = 1.0) -> sqlite3.Connection:
    # Open with the SQLite URI 'ro' mode so we never write to (or lock)
    # the live database the Kobo itself uses.
    uri = f"file:{db_path}?mode=ro"
    # Right after the Kobo mounts, the volume can be briefly unready and
    # sqlite3 fails with "unable to open database file" even though the
    # file exists; retrying a few times clears it up.
    last_error: sqlite3.Error | None = None
    for attempt in range(retries):
        try:
            conn = sqlite3.connect(uri, uri=True)
            conn.execute("SELECT 1")  # force the file to actually be read
            return conn
        except sqlite3.Error as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(delay)
    sys.exit(f"Could not open Kobo database at {db_path}: {last_error}\n"
              f"It may still be syncing, or the file may be corrupted.")


def fetch_highlights(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.cursor()

    def table_columns(table: str) -> set[str]:
        cur.execute("PRAGMA table_info(%s)" % table)
        return {row[1] for row in cur.fetchall()}

    bookmark_cols = table_columns("Bookmark")
    if not bookmark_cols:
        sys.exit("Bookmark table not found in the Kobo database. "
                  "Kobo firmware may have changed its schema.")

    required = ["BookmarkID", "Text", "Annotation", "DateCreated", "VolumeID"]
    missing = [c for c in required if c not in bookmark_cols]
    if missing:
        sys.exit(f"Bookmark table is missing expected columns: {missing}. "
                  f"Kobo firmware may have changed its schema.")

    # `content` is joined for title/author but isn't essential; degrade
    # gracefully instead of crashing if it's absent or reshaped.
    content_cols = table_columns("content")
    has_content = {"ContentID", "Title", "Attribution"} <= content_cols

    if has_content:
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
    else:
        query = """
            SELECT BookmarkID, Text, Annotation, DateCreated, VolumeID
            FROM Bookmark
            WHERE Text IS NOT NULL AND TRIM(Text) != ''
            ORDER BY DateCreated
        """

    try:
        cur.execute(query)
        rows = cur.fetchall()
    except sqlite3.DatabaseError as exc:
        sys.exit(f"Failed to read highlights from the Kobo database: {exc}")

    quotes = []
    for row in rows:
        if has_content:
            bookmark_id, text, annotation, date_created, volume_id, title, author = row
        else:
            bookmark_id, text, annotation, date_created, volume_id = row
            title = author = None
        if not bookmark_id or not text or not text.strip():
            continue  # skip malformed rows rather than writing unusable quotes
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


def _quarantine_corrupt_file(path: Path) -> None:
    """Move an unreadable file aside instead of silently discarding it, so
    the user can inspect or recover it later."""
    backup = path.with_suffix(path.suffix + f".corrupt-{int(time.time())}")
    try:
        path.rename(backup)
        print(f"Warning: {path} was unreadable; moved it to {backup} and starting fresh.",
              file=sys.stderr)
    except OSError as exc:
        print(f"Warning: {path} was unreadable and couldn't be backed up ({exc}); "
              f"starting fresh.", file=sys.stderr)


def load_whitelist(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        print(f"Warning: could not read whitelist at {path}: {exc}", file=sys.stderr)
        _quarantine_corrupt_file(path)
        return {}
    if not isinstance(data, dict):
        print(f"Warning: whitelist at {path} was not a JSON object; ignoring it.",
              file=sys.stderr)
        _quarantine_corrupt_file(path)
        return {}

    cleaned = {}
    for vid, info in data.items():
        if (
            isinstance(info, dict)
            and info.get("decision") in ("approved", "rejected")
        ):
            cleaned[vid] = info
        else:
            print(f"Warning: dropping malformed whitelist entry for {vid!r}; "
                  f"it will be asked about again.", file=sys.stderr)
    return cleaned


def save_whitelist(path: Path, whitelist: dict) -> None:
    write_atomic(path, whitelist)


def resolve_whitelist(fresh: list[dict], whitelist: dict, assume_no: bool = False) -> None:
    """Prompt for any book not yet decided. Mutates `whitelist` in place so
    partial progress is kept even if the user bails out partway through.

    If stdin isn't a TTY (e.g. run from cron/launchd) or `assume_no` is set,
    undecided books are left pending rather than blocking on input()."""
    books = {}
    order = []
    for q in fresh:
        vid = q["volume_id"]
        if vid not in books:
            books[vid] = {"title": q["book"], "author": q["author"], "count": 0}
            order.append(vid)
        books[vid]["count"] += 1

    pending = [vid for vid in order if vid not in whitelist]
    if not pending:
        return

    if assume_no or not sys.stdin.isatty():
        print(f"{len(pending)} new book(s) found but running non-interactively; "
              f"leaving them undecided (re-run interactively to approve them).",
              file=sys.stderr)
        return

    for vid in pending:
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
    by_id = {q["id"]: q for q in existing if isinstance(q, dict) and "id" in q}
    for q in fresh:
        by_id[q["id"]] = q  # fresh data wins if a highlight was edited on-device
    return sorted(by_id.values(), key=lambda q: q.get("date") or "")


def load_existing_quotes(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        print(f"Warning: could not read existing output at {path}: {exc}", file=sys.stderr)
        _quarantine_corrupt_file(path)
        return []
    if not isinstance(data, list):
        print(f"Warning: existing output at {path} was not a JSON array; ignoring it.",
              file=sys.stderr)
        _quarantine_corrupt_file(path)
        return []
    return data


def write_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
        ) as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)  # atomic on the same filesystem
    except OSError:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="Path to KoboReader.sqlite "
                         "(default: auto-detect under /Volumes)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                         help=f"Output JSON path (default: {DEFAULT_OUT})")
    parser.add_argument("--whitelist", type=Path, default=DEFAULT_WHITELIST,
                         help=f"Book whitelist JSON path (default: {DEFAULT_WHITELIST})")
    parser.add_argument("--non-interactive", action="store_true",
                         help="Never prompt for new books (e.g. when run from cron/launchd); "
                              "undecided books are left pending for a future interactive run.")
    args = parser.parse_args()

    db_path = args.db or find_kobo_db()
    if db_path is None:
        sys.exit("Could not find KoboReader.sqlite. Plug in your Kobo via USB, "
                  "or pass --db explicitly.")
    if not db_path.exists():
        sys.exit(f"No such file: {db_path}")
    if not os.access(db_path, os.R_OK):
        sys.exit(f"No permission to read {db_path}.")

    # sqlite3 needs a real path (not a Path object) inside the URI.
    conn = open_readonly(db_path)
    try:
        fresh = fetch_highlights(conn)
    except sqlite3.Error as exc:
        sys.exit(f"Failed to read the Kobo database: {exc}")
    finally:
        conn.close()

    whitelist = load_whitelist(args.whitelist)
    resolve_whitelist(fresh, whitelist, assume_no=args.non_interactive)
    try:
        save_whitelist(args.whitelist, whitelist)
    except OSError as exc:
        sys.exit(f"Failed to save whitelist to {args.whitelist}: {exc}")

    approved_ids = {vid for vid, info in whitelist.items() if info["decision"] == "approved"}
    fresh_approved = [
        {k: v for k, v in q.items() if k != "volume_id"}
        for q in fresh if q["volume_id"] in approved_ids
    ]

    existing = load_existing_quotes(args.out)
    combined = merge(existing, fresh_approved)
    try:
        write_atomic(args.out, combined)
    except OSError as exc:
        sys.exit(f"Failed to write output to {args.out}: {exc}")

    pending = {q["volume_id"] for q in fresh} - set(whitelist)
    print(f"Read {len(fresh)} highlights from {db_path}")
    print(f"Included {len(fresh_approved)} highlights from approved books")
    if pending:
        print(f"{len(pending)} book(s) still undecided; you'll be asked again next run")
    print(f"Wrote {len(combined)} total quotes to {args.out}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nInterrupted.")
