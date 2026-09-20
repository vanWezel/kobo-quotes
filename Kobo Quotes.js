// Variables used by Scriptable.
// icon-color: deep-purple; icon-glyph: quote-right;

async function main() {
  try {
    const fm = FileManager.iCloud();
    const path = fm.joinPath(fm.documentsDirectory(), "quotes.json");

    if (!fm.fileExists(path)) {
      return buildWidget("No quotes.json yet — run the export script on your Mac.");
    }

    if (!fm.isFileDownloaded(path)) {
      try {
        await fm.downloadFileFromiCloud(path);
      } catch (e) {
        return buildWidget("Couldn't download quotes.json from iCloud. Try again later.");
      }
    }

    let raw;
    try {
      raw = fm.readString(path);
    } catch (e) {
      return buildWidget("Couldn't read quotes.json.");
    }

    let quotes;
    try {
      quotes = JSON.parse(raw);
    } catch (e) {
      return buildWidget("quotes.json is corrupted — re-run the export script.");
    }

    if (!Array.isArray(quotes) || quotes.length === 0) {
      return buildWidget("No highlights found yet.");
    }

    const usable = quotes.filter(
      (q) => q && typeof q.text === "string" && q.text.trim() !== ""
    );
    if (usable.length === 0) {
      return buildWidget("No usable highlights found yet.");
    }

    const quote = usable[Math.floor(Math.random() * usable.length)];
    return buildWidget(quote.text, quote.note, quote.book, quote.author);
  } catch (e) {
    return buildWidget("Something went wrong loading quotes.");
  }
}

function buildWidget(text, note, book, author) {
  const w = new ListWidget();
  w.backgroundColor = new Color("#1c1c1e");
  w.setPadding(16, 16, 16, 16);

  const quoteText = w.addText(`“${text}”`);
  quoteText.textColor = Color.white();
  quoteText.font = Font.italicSystemFont(15);
  quoteText.minimumScaleFactor = 0.6;

  if (note) {
    w.addSpacer(8);
    const noteText = w.addText(note);
    noteText.textColor = new Color("#a1a1a6");
    noteText.font = Font.systemFont(13);
    noteText.minimumScaleFactor = 0.6;
  }

  if (book) {
    w.addSpacer(8);
    const label = author ? `— ${book}, ${author}` : `— ${book}`;
    const attribution = w.addText(label);
    attribution.textColor = new Color("#a1a1a6");
    attribution.font = Font.systemFont(12);
  }

  return w;
}

const widget = await main();
if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  await widget.presentMedium();
}
Script.complete();
