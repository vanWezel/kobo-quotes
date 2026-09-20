# Kobo Quotes

Turns highlights from a Kobo e-reader into a random-quote widget on iOS,
via a [Scriptable](https://scriptable.app) widget backed by a JSON file in
iCloud Drive.

## How it fits together

1. **`export_kobo_highlights.py`** — run on your Mac with the Kobo plugged
   in over USB. Reads highlights straight out of the device's
   `KoboReader.sqlite`, and writes them to `quotes.json` in Scriptable's
   iCloud container.
2. **`Kobo Quotes.js`** — a Scriptable widget. Reads `quotes.json` and shows
   a random highlight (with your note and the book/author) on your Home
   Screen.
3. **`book_whitelist.json`** — tracks which books you've approved for
   export, so a highlight from, say, a work doc doesn't end up on your
   Home Screen. Committed to this repo so the whitelist travels with it.

## Setup

### 1. Widget

- Install [Scriptable](https://apps.apple.com/app/scriptable/id1405459188)
  on your iPhone/iPad and open it once (so its iCloud container exists).
- Copy `Kobo Quotes.js` into Scriptable (via the app, or by placing the
  file in Scriptable's iCloud Drive folder).
- Add a Scriptable widget to your Home Screen and point it at the
  "Kobo Quotes" script.

### 2. Export script

Requires Python 3.10+ (stdlib only, no dependencies to install).

```
python3 export_kobo_highlights.py
```

- Auto-detects the Kobo's `KoboReader.sqlite` under `/Volumes` when the
  device is mounted via USB. Override with `--db` if needed.
- Defaults to writing `quotes.json` into Scriptable's iCloud container
  (`--out` to override).
- Defaults to `book_whitelist.json` next to the script (`--whitelist` to
  override).

The first time highlights from a new book show up, you'll be prompted:

```
New book: "Some Book" by Some Author (12 highlights)
Include its highlights in the export? [y/N/q]
```

Your answer is remembered in `book_whitelist.json`, so you're never asked
again for that book. To change your mind later, edit the file directly
(flip `"decision"` to `"approved"` or `"rejected"`) and re-run.

Re-running is always safe:
- Existing quotes (matched by `BookmarkID`) are kept as-is; edits made
  on-device overwrite the stored copy.
- New highlights are appended.
- Highlights deleted on the Kobo are *not* removed from `quotes.json` —
  deletions on-device don't delete history here.

### Running unattended (cron/launchd)

Pass `--non-interactive` to skip the approve/reject prompt. New books are
left undecided (and excluded from the export) until you run the script
interactively to approve or reject them — the script never hangs waiting
for input.

## Output format

`quotes.json` is a flat array:

```json
[
  {
    "id": "...",
    "text": "The highlighted passage.",
    "note": "Your annotation, or null",
    "book": "Book title",
    "author": "Author name, or null",
    "date": "2024-01-01T12:00:00.000"
  }
]
```
