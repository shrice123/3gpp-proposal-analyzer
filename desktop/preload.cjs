const { contextBridge, ipcRenderer } = require("electron");

const apiArg = process.argv.find((value) => value.startsWith("--proposal-api-base="));
const apiBase = apiArg ? apiArg.slice("--proposal-api-base=".length) : "http://127.0.0.1:8765";

contextBridge.exposeInMainWorld("proposalDesktop", {
  apiBase,
  showItemInFolder: (path) => ipcRenderer.invoke("file:show-in-folder", path),
  chooseDataDirectory: () => ipcRenderer.invoke("data:choose-directory"),
  repairRuntime: () => ipcRenderer.invoke("runtime:repair"),
});
