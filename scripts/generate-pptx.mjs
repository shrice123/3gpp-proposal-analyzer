import fs from "node:fs/promises";
import pptxgen from "pptxgenjs";

const [payloadPath, outputPath] = process.argv.slice(2);
if (!payloadPath || !outputPath) throw new Error("Usage: generate-pptx.mjs payload.json output.pptx");
const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
const deck = new pptxgen();
deck.layout = "LAYOUT_WIDE";
deck.author = "3GPP Proposal Analyzer";
deck.subject = "3GPP proposal analysis";
deck.title = payload.title;
deck.company = "Proposal Insight";
deck.lang = "en-US";
deck.theme = {
  headFontFace: "Aptos Display",
  bodyFontFace: "Aptos",
  lang: "en-US",
};

for (const [index, item] of payload.slides.entries()) {
  const slide = deck.addSlide();
  slide.background = { color: index === 0 ? "16364A" : "F7F8F5" };
  if (index === 0) {
    slide.addText(item.title || payload.title, { x: 0.9, y: 2.25, w: 10.7, h: 1.2, fontFace: "Aptos Display", fontSize: 34, bold: false, color: "FFFFFF", margin: 0, breakLine: false });
    slide.addText(item.content || "Selected 3GPP proposal analysis", { x: 0.92, y: 3.65, w: 8.2, h: 0.55, fontSize: 17, color: "D8F281", margin: 0 });
  } else {
    slide.addText(item.title || `Analysis ${index}`, { x: 0.72, y: 0.62, w: 11.75, h: 0.62, fontFace: "Aptos Display", fontSize: 25, color: "16364A", bold: true, margin: 0 });
    const lines = String(item.content || "").split("\n").filter(Boolean).slice(0, 8);
    slide.addText(lines.map((text) => ({ text, options: { bullet: { indent: 18 }, breakLine: true } })), { x: 0.9, y: 1.65, w: 11.2, h: 4.9, fontFace: "Aptos", fontSize: 17, color: "314D5B", breakLine: true, valign: "top", margin: 0.04, paraSpaceAfterPt: 12 });
    slide.addText(String(index).padStart(2, "0"), { x: 11.9, y: 7.05, w: 0.6, h: 0.22, fontSize: 9, color: "7A8B94", align: "right", margin: 0 });
  }
}
await deck.writeFile({ fileName: outputPath });

