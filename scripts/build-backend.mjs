import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const candidates = process.platform === "win32"
  ? [path.join(root, ".venv", "Scripts", "python.exe"), "python"]
  : [path.join(root, ".venv", "bin", "python"), "python3"];
const python = candidates.find((candidate) => candidate.includes(path.sep) ? fs.existsSync(candidate) : true);
const result = spawnSync(python, ["-m", "PyInstaller", "--clean", "proposal-backend.spec"], {
  cwd: path.join(root, "backend"),
  stdio: "inherit",
  env: { ...process.env, PYINSTALLER_CONFIG_DIR: path.join(root, "backend", ".pyinstaller") },
});
process.exit(result.status ?? 1);
