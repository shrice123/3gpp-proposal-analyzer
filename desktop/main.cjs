const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { autoUpdater } = require("electron-updater");
const { spawn } = require("node:child_process");
const net = require("node:net");
const path = require("node:path");
const { ensureRuntimeComponents } = require("./runtime-manager.cjs");

let backend = null;
let mainWindow = null;

function findPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => resolve(address.port));
    });
  });
}

function backendCommand(port) {
  if (app.isPackaged) {
    const executable = process.platform === "win32" ? "proposal-backend.exe" : "proposal-backend";
    return {
      command: path.join(process.resourcesPath, "backend", executable),
      args: [],
      cwd: path.join(process.resourcesPath, "backend"),
      env: {
        ...process.env,
        PROPOSAL_TOOL_PORT: String(port),
        PROPOSAL_NODE_PATH: process.execPath,
        PROPOSAL_NODE_IS_ELECTRON: "1",
        PROPOSAL_PPTX_SCRIPT: path.join(process.resourcesPath, "app.asar", "scripts", "generate-pptx.mjs"),
      },
    };
  }
  return {
    command: process.env.PROPOSAL_PYTHON || "python3",
    args: [path.join(__dirname, "..", "backend", "run.py")],
    cwd: path.join(__dirname, "..", "backend"),
    env: {
      ...process.env,
      PROPOSAL_TOOL_PORT: String(port),
      PROPOSAL_NODE_PATH: process.execPath,
      PROPOSAL_NODE_IS_ELECTRON: "1",
      PROPOSAL_PPTX_SCRIPT: path.join(__dirname, "..", "scripts", "generate-pptx.mjs"),
    },
  };
}

async function waitForBackend(url) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${url}/api/health`);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
  throw new Error("本地分析服务启动超时");
}

async function createWindow() {
  await ensureRuntimeComponents({ repair: false });
  const port = await findPort();
  const apiBase = `http://127.0.0.1:${port}`;
  const command = backendCommand(port);
  backend = spawn(command.command, command.args, {
    cwd: command.cwd,
    env: command.env,
    stdio: app.isPackaged ? "ignore" : "inherit",
    windowsHide: true,
  });
  backend.on("exit", (code) => {
    if (!app.isQuitting && mainWindow) {
      dialog.showErrorBox("本地服务已停止", `分析服务意外退出（${code ?? "unknown"}）。请重新启动应用。`);
    }
  });
  await waitForBackend(apiBase);

  mainWindow = new BrowserWindow({
    width: 1460,
    height: 940,
    minWidth: 980,
    minHeight: 720,
    show: false,
    backgroundColor: "#f7f8f5",
    title: "3GPP 提案洞察",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      additionalArguments: [`--proposal-api-base=${apiBase}`],
    },
  });
  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
  if (process.env.PROPOSAL_RENDERER_URL) await mainWindow.loadURL(process.env.PROPOSAL_RENDERER_URL);
  else await mainWindow.loadFile(path.join(__dirname, "..", "desktop-dist", "index.html"));
}

ipcMain.handle("data:choose-directory", async () => {
  const result = await dialog.showOpenDialog(mainWindow, { title: "选择数据存储位置", properties: ["openDirectory", "createDirectory"] });
  return result.canceled ? null : result.filePaths[0];
});
ipcMain.handle("file:show-in-folder", async (_, filePath) => shell.showItemInFolder(filePath));
ipcMain.handle("runtime:repair", async () => ensureRuntimeComponents({ repair: true }));

const hasLock = app.requestSingleInstanceLock();
if (!hasLock) app.quit();
else {
  app.on("second-instance", () => {
    if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.focus(); }
  });
  app.whenReady().then(async () => {
    try {
      await createWindow();
      if (app.isPackaged && process.env.PROPOSAL_UPDATE_URL) {
        autoUpdater.setFeedURL({ provider: "generic", url: process.env.PROPOSAL_UPDATE_URL });
        autoUpdater.checkForUpdatesAndNotify().catch(() => {});
      }
    } catch (error) {
      dialog.showErrorBox("无法启动 3GPP 提案洞察", error instanceof Error ? error.message : String(error));
      app.quit();
    }
  });
}

app.on("before-quit", () => {
  app.isQuitting = true;
  if (backend && !backend.killed) backend.kill();
});
app.on("window-all-closed", () => app.quit());
