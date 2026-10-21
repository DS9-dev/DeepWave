// Безопасный мост между интерфейсом и главным процессом: только перечисленные функции.
import { contextBridge, ipcRenderer } from "electron";

export type BackendInfo = { url: string; token: string; error: string; logPath: string };
export type WinMode = "full" | "compact";
export type CacheInfo = { httpBytes: number; httpLimit: number; logBytes: number };

contextBridge.exposeInMainWorld("musicApp", {
  backend: (): Promise<BackendInfo> => ipcRenderer.invoke("backend:info"),
  restartBackend: (): Promise<BackendInfo> => ipcRenderer.invoke("backend:restart"),
  onBackend: (cb: (i: BackendInfo) => void) => { ipcRenderer.on("backend:changed", (_e, i) => cb(i)); },
  win: {
    minimize: () => ipcRenderer.send("win:minimize"),
    toggleMaximize: () => ipcRenderer.send("win:toggle-maximize"),
    close: () => ipcRenderer.send("win:close"),
    isMaximized: (): Promise<boolean> => ipcRenderer.invoke("win:is-maximized"),
    onMaximized: (cb: (m: boolean) => void) => { ipcRenderer.on("win:maximized", (_e, m) => cb(m)); },
    getMode: (): Promise<{ mode: WinMode; pinned: boolean }> => ipcRenderer.invoke("win:get-mode"),
    setMode: (m: WinMode) => ipcRenderer.send("win:set-mode", m),
    onMode: (cb: (m: WinMode) => void) => { ipcRenderer.on("win:mode", (_e, m) => cb(m)); },
    setPinned: (p: boolean): Promise<boolean> => ipcRenderer.invoke("win:set-pinned", p),
  },
  openExternal: (url: string) => ipcRenderer.invoke("open-external", url),
  showLog: () => ipcRenderer.invoke("show-log"),
  cache: {
    info: (): Promise<CacheInfo> => ipcRenderer.invoke("cache:info"),
    clear: (): Promise<CacheInfo> => ipcRenderer.invoke("cache:clear"),
  },
  smoke: {
    enabled: (): Promise<boolean> => ipcRenderer.invoke("smoke:enabled"),
    rendered: (what: string) => ipcRenderer.send("smoke:rendered", what),
  },
  platform: process.platform,
});
