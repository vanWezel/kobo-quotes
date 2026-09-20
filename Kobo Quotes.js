// Variables used by Scriptable.
// icon-color: deep-purple; icon-glyph: quote-right;

async function main() {
  const fm = FileManager.iCloud();
  const path = fm.joinPath(fm.documentsDirectory(), "quotes.json");

  if (!fm.fileExists(path)) {
    return buildWidget("No quotes.json yet — run the export script on your Mac.");
  }
  if (!fm.isFileDownloaded(path)) {
    await fm.downloadFileFromiCloud(path);
  }

  const quotes = JSON.parse(fm.readString(path));
  if (!quotes || quotes.length === 0) {
    return buildWidget("No highlights found yet.");
  }

  const quote = quotes[Math.floor(Math.random() * quotes.length)];
  return buildWidget(quote.text, quote.note, quote.book, quote.author);
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
