// The only bridge from pages to the shell. The main process checks the sender again.
import { contextBridge, ipcRenderer, type IpcRendererEvent } from "electron";

contextBridge.exposeInMainWorld("okbd2Shell", {
  getStatus: () => ipcRenderer.invoke("shell:get-status"),
  onStatus: (callback: (status: unknown) => void) => {
    const listener = (_event: IpcRendererEvent, status: unknown) => callback(status);
    ipcRenderer.on("shell:status", listener);
    return () => ipcRenderer.removeListener("shell:status", listener);
  },
  info: () => ipcRenderer.invoke("shell:info"),
  restartCore: () => ipcRenderer.invoke("shell:restart-core"),
  openLogsFolder: () => ipcRenderer.invoke("shell:open-logs"),
  quit: () => ipcRenderer.invoke("shell:quit"),
});
