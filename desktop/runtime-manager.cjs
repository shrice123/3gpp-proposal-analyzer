const { app } = require("electron");
const crypto = require("node:crypto");
const fs = require("node:fs");
const fsp = require("node:fs/promises");
const path = require("node:path");
const { pipeline } = require("node:stream/promises");
const { Readable } = require("node:stream");

function sha256(filePath) {
  return new Promise((resolve, reject) => {
    const digest = crypto.createHash("sha256");
    const stream = fs.createReadStream(filePath);
    stream.on("data", (chunk) => digest.update(chunk));
    stream.on("error", reject);
    stream.on("end", () => resolve(digest.digest("hex")));
  });
}

function matches(component) {
  const platform = component.platform || "all";
  const arch = component.arch || "all";
  return (platform === "all" || platform === process.platform) && (arch === "all" || arch === process.arch);
}

async function readManifest() {
  const bundled = app.isPackaged
    ? path.join(process.resourcesPath, "runtime-bundle", "manifest.json")
    : path.join(__dirname, "..", "runtime-bundle", "manifest.json");
  try { return JSON.parse(await fsp.readFile(bundled, "utf8")); }
  catch { return { schema_version: 1, components: [] }; }
}

async function ensureRuntimeComponents({ repair = false } = {}) {
  const manifest = await readManifest();
  const runtimeRoot = path.join(app.getPath("userData"), "runtime");
  await fsp.mkdir(runtimeRoot, { recursive: true });
  const results = [];
  for (const component of manifest.components.filter(matches)) {
    const destination = path.join(runtimeRoot, component.file);
    let valid = false;
    try { valid = (await sha256(destination)) === component.sha256; } catch {}
    if (!valid && (repair || component.required)) {
      await fsp.mkdir(path.dirname(destination), { recursive: true });
      const bundledSource = app.isPackaged
        ? path.join(process.resourcesPath, "runtime-bundle", component.file)
        : path.join(__dirname, "..", "runtime-bundle", component.file);
      if (fs.existsSync(bundledSource)) await fsp.copyFile(bundledSource, destination);
      else if (component.url) {
        const response = await fetch(component.url);
        if (!response.ok || !response.body) throw new Error(`${component.name} 下载失败（HTTP ${response.status}）`);
        await pipeline(Readable.fromWeb(response.body), fs.createWriteStream(destination));
      }
      valid = (await sha256(destination)) === component.sha256;
      if (!valid) { await fsp.rm(destination, { force: true }); throw new Error(`${component.name} 完整性校验失败`); }
    }
    results.push({ name: component.name, installed: valid, required: Boolean(component.required) });
  }
  return { root: runtimeRoot, components: results };
}

module.exports = { ensureRuntimeComponents };

