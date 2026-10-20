// DeepWave — главный процесс Electron: окно без системной рамки (обычный и компактный режим)
// и локальный Python-сервис, который живёт ровно столько, сколько приложение.
import { app, BrowserWindow, ipcMain, nativeTheme, screen, shell, dialog } from "electron";
import { spawn, spawnSync, ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { appendFileSync, copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

type Bounds = { x?: number; y?: number; width: number; height: number };
type Mode = "full" | "compact";
type Config = { python?: string; bounds?: Bounds; maximized?: boolean; mode?: Mode; compactPos?: { x: number; y: number }; pinned?: boolean };

const FULL_MIN = { width: 960, height: 620 };
const COMPACT = { width: 460, height: 156 };

const userDir = app.getPath("userData");                  // %APPDATA%\DeepWave
mkdirSync(userDir, { recursive: true });
const configPath = join(userDir, "config.json");
const logPath = join(userDir, "backend.log");
// перенос настроек окна из прежнего названия приложения (Music App)
const legacyConfig = join(app.getPath("appData"), "Music App", "config.json");
if (!existsSync(configPath) && existsSync(legacyConfig)) { try { copyFileSync(legacyConfig, configPath); } catch { /* ignore */ } }

const readConfig = (): Config => { try { return JSON.parse(readFileSync(configPath, "utf-8")); } catch { return {}; } };
const writeConfig = (patch: Partial<Config>) => { try { writeFileSync(configPath, JSON.stringify({ ...readConfig(), ...patch }, null, 2)); } catch { /* ignore */ } };
const blog = (s: string) => { try { appendFileSync(logPath, `[${new Date().toISOString()}] ${s}\n`); } catch { /* ignore */ } };

const smokeArg = process.argv.find((a) => a.startsWith("--smoke-test="));
const smokeDir = smokeArg ? smokeArg.split("=").slice(1).join("=") : "";

function projectRoot(): string {
  if (process.env.DEEPWAVE_ROOT) return process.env.DEEPWAVE_ROOT;
  if (app.isPackaged) {
    try { return JSON.parse(readFileSync(join(process.resourcesPath, "root.json"), "utf-8")).root; } catch { /* fallthrough */ }
  }
  return resolve(__dirname, "..", "..", "..");   // app/dist/main -> корень проекта
}

// ------------------------------------------------------------------ Python-сервис
const backend = { url: "", token: randomBytes(24).toString("hex"), error: "", proc: null as ChildProcess | null, restarts: 0, quitting: false };

function findPython(root: string): string[] | null {
  const cfg = readConfig();
  const candidates: string[][] = [];
  if (cfg.python) candidates.push([cfg.python]);
  candidates.push(["py", "-3"], ["python"], ["python3"]);
  for (const c of candidates) {
    const r = spawnSync(c[0], [...c.slice(1), "-c", "import yandex_music, requests, sys; print(sys.executable)"],
      { cwd: root, encoding: "utf-8", windowsHide: true, timeout: 20000 });
    if (r.status === 0 && r.stdout.trim()) {
      const exe = r.stdout.trim().split(/\r?\n/).pop()!;
      if (cfg.python !== exe) writeConfig({ python: exe });
      return [exe];
    }
  }
  return null;
}

function startBackend(): Promise<void> {
  return new Promise((done) => {
    const root = projectRoot();
    const server = join(root, "backend", "server.py");
    if (!existsSync(server)) { backend.error = `Не найден ${server}`; return done(); }
    const py = findPython(root);
    if (!py) {
      backend.error = "Не найден Python с библиотекой yandex-music. Установите: python -m pip install -U yandex-music";
      return done();
    }
    blog(`start: ${py.join(" ")} ${server}`);
    // stdin остаётся открытым: если DeepWave завершится аварийно, сервис увидит конец потока и закроется сам
    const proc = spawn(py[0], [...py.slice(1), server, "--port", "0", "--root", root, "--watch-stdin"], {
      cwd: root, windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, DEEPWAVE_TOKEN: backend.token, MUSIC_APP_TOKEN: backend.token, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" },
    });
    backend.proc = proc;
    let settled = false;
    const finish = () => { if (!settled) { settled = true; done(); } };
    const timer = setTimeout(() => { backend.error = "Сервис не ответил за 40 с (см. backend.log)"; finish(); }, 40000);
    proc.stdout?.setEncoding("utf-8");
    proc.stdout?.on("data", (chunk: string) => {
      const m = /READY (\d+)/.exec(chunk);
      if (m) { backend.url = `http://127.0.0.1:${m[1]}`; backend.error = ""; clearTimeout(timer); finish(); notify(); }
    });
    proc.stderr?.setEncoding("utf-8");
    proc.stderr?.on("data", (s: string) => blog(s.trimEnd()));
    proc.on("exit", (code) => {
      blog(`exit ${code}`);
      backend.url = "";
      clearTimeout(timer);
      if (!backend.quitting && backend.restarts < 3) {
        backend.restarts += 1;
        backend.error = "Сервис перезапускается…";
        notify();
        setTimeout(() => startBackend().then(notify), 1500);
      } else if (!backend.quitting) {
        backend.error = `Сервис остановился (код ${code}). Подробности: ${logPath}`;
        notify();
      }
      finish();
    });
  });
}

async function stopBackend() {
  backend.quitting = true;
  const p = backend.proc;
  if (!p || p.exitCode !== null) return;
  try {
    // просим сервис завершиться: сборщик дописывает текущую задачу
    await fetch(`${backend.url}/api/shutdown`, { method: "POST", headers: { "X-Token": backend.token }, signal: AbortSignal.timeout(3000) });
  } catch { /* ignore */ }
  try { p.stdin?.end(); } catch { /* ignore */ }
  await new Promise<void>((r) => {
    const t = setTimeout(() => { try { p.kill(); } catch { /* */ } r(); }, 8000);
    p.once("exit", () => { clearTimeout(t); r(); });
  });
}
// последняя страховка: процесс сервиса не переживает DeepWave
process.on("exit", () => { try { if (backend.proc && backend.proc.exitCode === null) backend.proc.kill(); } catch { /* */ } });

// ------------------------------------------------------------------ окно и режимы
let win: BrowserWindow | null = null;
let mode: Mode = readConfig().mode === "compact" ? "compact" : "full";
const notify = () => win?.webContents.send("backend:changed", backendInfo());
const backendInfo = () => ({ url: backend.url, token: backend.token, error: backend.error, logPath });

function compactPosition(): { x: number; y: number } {
  const saved = readConfig().compactPos;
  const area = screen.getDisplayNearestPoint(saved ?? screen.getCursorScreenPoint()).workArea;
  if (saved && saved.x >= area.x && saved.y >= area.y && saved.x + COMPACT.width <= area.x + area.width && saved.y + COMPACT.height <= area.y + area.height) return saved;
  return { x: area.x + area.width - COMPACT.width - 24, y: area.y + area.height - COMPACT.height - 24 };
}

function applyMode(next: Mode, initial = false) {
  if (!win) return;
  const cfg = readConfig();
  if (next === "compact") {
    if (!initial && mode === "full") writeConfig({ maximized: win.isMaximized(), ...(win.isMaximized() ? {} : { bounds: win.getBounds() }) });
    if (win.isMaximized()) win.unmaximize();
    win.setMinimumSize(COMPACT.width, COMPACT.height);
    win.setResizable(false);
    win.setMaximizable(false);
    const p = compactPosition();
    win.setBounds({ x: p.x, y: p.y, width: COMPACT.width, height: COMPACT.height });
    win.setAlwaysOnTop(!!cfg.pinned, "floating");
  } else {
    if (!initial && mode === "compact") writeConfig({ compactPos: { x: win.getBounds().x, y: win.getBounds().y } });
    win.setAlwaysOnTop(false);
    win.setResizable(true);
    win.setMaximizable(true);
    win.setMinimumSize(FULL_MIN.width, FULL_MIN.height);
    const b = cfg.bounds;
    if (b && b.width >= FULL_MIN.width) win.setBounds(b.x !== undefined && b.y !== undefined ? { x: b.x, y: b.y, width: b.width, height: b.height } : { width: b.width, height: b.height });
    else { win.setSize(1280, 820); win.center(); }
    if (cfg.maximized) win.maximize();
  }
  mode = next;
  writeConfig({ mode });
  win.webContents.send("win:mode", mode);
}

function createWindow() {
  const cfg = readConfig();
  const full = { width: cfg.bounds?.width ?? 1280, height: cfg.bounds?.height ?? 820, x: cfg.bounds?.x, y: cfg.bounds?.y };
  win = new BrowserWindow({
    ...full, minWidth: FULL_MIN.width, minHeight: FULL_MIN.height, frame: false, show: false, title: "DeepWave",
    backgroundColor: nativeTheme.shouldUseDarkColors ? "#1b1b1f" : "#f4f4f6",
    webPreferences: { preload: join(__dirname, "preload.js"), contextIsolation: true, sandbox: true, nodeIntegration: false, spellcheck: false },
  });
  if (mode === "compact") applyMode("compact", true);
  else if (cfg.maximized) win.maximize();
  win.loadFile(join(__dirname, "..", "renderer", "index.html"));
  win.once("ready-to-show", () => { if (!smokeDir) win?.show(); else win?.showInactive(); });
  const sendMax = () => win?.webContents.send("win:maximized", win.isMaximized());
  win.on("maximize", sendMax);
  win.on("unmaximize", sendMax);
  const save = () => {
    if (!win || win.isMinimized()) return;
    if (mode === "compact") writeConfig({ compactPos: { x: win.getBounds().x, y: win.getBounds().y } });
    else if (!win.isMaximized()) writeConfig({ bounds: win.getBounds(), maximized: false });
  };
  win.on("resized", save);
  win.on("moved", save);
  win.on("close", () => { if (win && mode === "full") writeConfig({ maximized: win.isMaximized() }); });
  // никакой навигации внутри окна; внешние ссылки — в браузере
  win.webContents.on("will-navigate", (e) => e.preventDefault());
  win.webContents.setWindowOpenHandler(({ url }) => { if (/^https:\/\//.test(url)) shell.openExternal(url); return { action: "deny" }; });
}

// ------------------------------------------------------------------ самопроверка сборки (--smoke-test=<папка>)
async function smokeTest() {
  const out: Record<string, unknown> = { started: new Date().toISOString(), electron: process.versions.electron, root: projectRoot() };
  const finish = (ok: boolean, why: string) => {
    out.ok = ok; out.reason = why; out.finished = new Date().toISOString(); out.backend = backendInfo().url ? "ready" : backend.error;
    try { mkdirSync(smokeDir, { recursive: true }); writeFileSync(join(smokeDir, "result.json"), JSON.stringify(out, null, 2)); } catch { /* */ }
    stopBackend().finally(() => app.exit(ok ? 0 : 1));
  };
  const timeout = setTimeout(() => finish(false, "timeout 90s"), 90000);
  const rendered = new Promise<string>((r) => ipcMain.once("smoke:rendered", (_e, what: string) => r(what)));
  const what = await rendered;
  out.renderer = what;
  const shot = async (name: string) => {
    await new Promise((r) => setTimeout(r, 1500));
    const img = await win!.webContents.capturePage();
    writeFileSync(join(smokeDir, `${name}.png`), img.toPNG());
    out[`${name}_size`] = win!.getBounds();
  };
  mkdirSync(smokeDir, { recursive: true });
  await shot("full");
  applyMode("compact");
  await shot("compact");
  applyMode("full");
  await shot("full_again");
  clearTimeout(timeout);
  finish(!!backend.url && what === "app", backend.url ? `renderer: ${what}` : `backend: ${backend.error}`);
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.show(); win.focus(); } });
  app.whenReady().then(async () => {
    ipcMain.handle("backend:info", () => backendInfo());
    ipcMain.handle("backend:restart", async () => { backend.restarts = 0; backend.quitting = false; await startBackend(); return backendInfo(); });
    ipcMain.on("win:minimize", () => win?.minimize());
    ipcMain.on("win:toggle-maximize", () => { if (!win || mode === "compact") return; if (win.isMaximized()) win.unmaximize(); else win.maximize(); });
    ipcMain.on("win:close", () => win?.close());
    ipcMain.handle("win:is-maximized", () => win?.isMaximized() ?? false);
    ipcMain.handle("win:get-mode", () => ({ mode, pinned: !!readConfig().pinned }));
    ipcMain.on("win:set-mode", (_e, m: Mode) => { if (m !== mode && (m === "full" || m === "compact")) applyMode(m); });
    ipcMain.handle("win:set-pinned", (_e, p: boolean) => { writeConfig({ pinned: !!p }); if (mode === "compact") win?.setAlwaysOnTop(!!p, "floating"); return !!p; });
    ipcMain.handle("open-external", (_e, url: string) => { if (/^https:\/\//.test(url)) shell.openExternal(url); });
    ipcMain.handle("show-log", () => shell.showItemInFolder(logPath));
    ipcMain.handle("smoke:enabled", () => !!smokeDir);
    createWindow();
    if (smokeDir) smokeTest();
    await startBackend();
    notify();
    if (backend.error && !backend.url && !smokeDir) dialog.showErrorBox("DeepWave", backend.error);
  });
  let quitting = false;
  app.on("before-quit", (e) => {
    if (quitting) return;
    e.preventDefault();
    quitting = true;
    stopBackend().finally(() => app.quit());
  });
  app.on("window-all-closed", () => app.quit());
}
