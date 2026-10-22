import { Component, createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, backendState, bridge, Catalog, onBackendChange, Settings, Status, WinMode } from "../api";
import { CompactPlayer } from "./Compact";
import { player } from "../player";
import { usePlayer } from "./common";
import { ICatalog, IHistory, ILibrary, IListen, ISettings } from "./icons";
import { PlayerBar } from "./PlayerBar";
import { ListenView } from "./views/Listen";
import { LibraryView } from "./views/Library";
import { ArtistView, AlbumView } from "./views/Detail";
import { HistoryView } from "./views/History";
import { CatalogView } from "./views/CatalogView";
import { SettingsView } from "./views/SettingsView";
import { SignIn } from "./views/SignIn";

export type Route = { view: "listen" | "library" | "history" | "catalog" | "settings" | "artist" | "album"; id?: number; q?: string };

type Ctx = {
  status: Status | null; catalogs: Catalog[]; settings: Settings | null;
  route: Route; go: (r: Route) => void; back: () => void; canBack: boolean;
  refresh: () => Promise<void>; saveSettings: (p: Partial<Settings>) => Promise<void>;
  genreTitle: (key: string) => string;
  mode: WinMode; setMode: (m: WinMode) => void;
};
const AppCtx = createContext<Ctx>(null as unknown as Ctx);
export const useApp = () => useContext(AppCtx);

function TitleBar() {
  const [max, setMax] = useState(false);
  useEffect(() => { bridge?.win.isMaximized().then(setMax); bridge?.win.onMaximized(setMax); }, []);
  return (
    <div className="titlebar" onDoubleClick={() => bridge?.win.toggleMaximize()}>
      <div className="lights">
        <button className="light close" title="Закрыть" onClick={() => bridge?.win.close()}><svg viewBox="0 0 10 10"><path d="M2.5 2.5l5 5M7.5 2.5l-5 5" /></svg></button>
        <button className="light min" title="Свернуть" onClick={() => bridge?.win.minimize()}><svg viewBox="0 0 10 10"><path d="M2 5h6" /></svg></button>
        <button className="light max" title={max ? "Восстановить" : "Развернуть"} onClick={() => bridge?.win.toggleMaximize()}>
          <svg viewBox="0 0 10 10">{max ? <path d="M3 7V3h4M7 3v4H3" /> : <path d="M2.6 7.4V2.6h4.8M7.4 2.6v4.8H2.6" />}</svg>
        </button>
      </div>
    </div>
  );
}

const NAV: [Route["view"], string, (p: { size?: number }) => React.ReactElement][] = [
  ["listen", "Слушать", IListen], ["library", "Библиотека", ILibrary], ["history", "История", IHistory],
  ["catalog", "Каталог", ICatalog], ["settings", "Настройки", ISettings],
];

function Sidebar() {
  const { route, go, status, catalogs } = useApp();
  const st = usePlayer();
  const tracks = catalogs.reduce((a, c) => a + (c.tracks || 0), 0);
  const top = ["artist", "album"].includes(route.view) ? "library" : route.view;
  return (
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark" />DeepWave</div>
      <nav>
        {NAV.map(([v, label, Icon]) => (
          <button key={v} className={`nav ${top === v ? "on" : ""}`} onClick={() => go({ view: v })}>
            <Icon size={18} /><span>{label}</span>
            {v === "catalog" && status?.crawl_running && <i className="pulse" title="Идёт сбор" />}
            {v === "listen" && st.playing && <span className="eq small"><i /><i /><i /></span>}
          </button>
        ))}
      </nav>
      <div className="side-foot">
        <div>{tracks.toLocaleString("ru-RU")} треков · {catalogs.length} {catalogs.length === 1 ? "каталог" : "каталога"}</div>
        {status?.account?.name && <div className="muted">{status.account.name}{status.account.has_plus ? " · Плюс" : ""}</div>}
      </div>
    </aside>
  );
}

function Boot({ error }: { error: string }) {
  return (
    <div className="boot">
      <span className="brand-mark big" />
      {error ? <>
        <h2>Сервис DeepWave не запустился</h2>
        <p className="muted">{error}</p>
        <div className="row gap">
          <button className="btn primary" onClick={() => bridge?.restartBackend()}>Попробовать снова</button>
          <button className="btn" onClick={() => bridge?.showLog()}>Открыть журнал</button>
        </div>
      </> : <><div className="spinner" /><p className="muted">Запуск…</p></>}
    </div>
  );
}

/** Ошибка отрисовки раздела показывается сообщением вместо пустого окна; плеер продолжает работать. */
class ViewBoundary extends Component<{ children: ReactNode }, { error: string }> {
  state = { error: "" };
  static getDerivedStateFromError(e: unknown) { return { error: String((e as Error)?.message || e) }; }
  componentDidCatch(e: unknown) { console.error("view error", e); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="card view-error">
        <h2>Раздел не удалось показать</h2>
        <p className="muted">{this.state.error}</p>
        <button className="btn" onClick={() => this.setState({ error: "" })}>Попробовать снова</button>
      </div>
    );
  }
}

export function App() {
  const [, force] = useState(0);
  useEffect(() => onBackendChange(() => force((n) => n + 1)), []);
  const [status, setStatus] = useState<Status | null>(null);
  const [catalogs, setCatalogs] = useState<Catalog[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [stack, setStack] = useState<Route[]>([{ view: "listen" }]);
  const route = stack[stack.length - 1];
  // режим окна: обычный или компактный (это одно и то же окно и тот же плеер)
  const [mode, setModeState] = useState<WinMode>(() => (new URLSearchParams(location.search).get("mode") === "compact" ? "compact" : "full"));
  useEffect(() => {
    if (!bridge) return;
    bridge.win.getMode().then((m) => setModeState(m.mode));
    bridge.win.onMode(setModeState);
  }, []);
  const setMode = useCallback((m: WinMode) => { if (bridge) bridge.win.setMode(m); else setModeState(m); }, []);
  useEffect(() => { document.documentElement.dataset.mode = mode; }, [mode]);

  const refresh = useCallback(async () => {
    try {
      const [s, c] = await Promise.all([api.get<Status>("/api/status"), api.get<{ catalogs: Catalog[] }>("/api/catalogs")]);
      setStatus(s); setCatalogs(c.catalogs);
      if (!settings) {
        const st = await api.get<Settings>("/api/settings");
        setSettings(st); player.setVolume(st.volume);
      }
    } catch { /* сервис ещё запускается */ }
  }, [settings]);

  useEffect(() => {
    if (!backendState.ready) return;
    refresh();
    const t = window.setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [backendState.ready, refresh]);

  const theme = settings?.theme ?? "auto";
  useEffect(() => { document.documentElement.dataset.theme = theme === "auto" ? "" : theme; }, [theme]);

  // громкость сохраняется с задержкой
  const pst = usePlayer();
  useEffect(() => {
    if (!settings || Math.abs(settings.volume - pst.volume) < 0.01) return;
    const t = window.setTimeout(() => saveSettings({ volume: Math.round(pst.volume * 100) / 100 }), 800);
    return () => clearTimeout(t);
  }, [pst.volume]);

  const saveSettings = useCallback(async (p: Partial<Settings>) => {
    const s = await api.post<Settings>("/api/settings", p);
    setSettings(s);
    if (Object.keys(p).some((k) => k.startsWith("country"))) player.refreshUpcoming();   // заранее выбранный трек — по новому фильтру
  }, []);

  const titles = useMemo(() => {
    const m = new Map<string, string>();
    catalogs.forEach((c) => c.genres.forEach((g) => m.set(g.key, g.title)));
    return m;
  }, [catalogs]);

  const ctx: Ctx = {
    status, catalogs, settings, route, refresh, saveSettings,
    go: (r) => setStack((s) => (s[s.length - 1].view === r.view && s[s.length - 1].id === r.id ? s : [...s.slice(-30), r])),
    back: () => setStack((s) => (s.length > 1 ? s.slice(0, -1) : s)),
    canBack: stack.length > 1,
    genreTitle: (k) => titles.get(k) || k.split(":").pop() || k,
    mode, setMode,
  };

  // самопроверка Windows-сборки: сообщить главному процессу, что интерфейс отрисован
  useEffect(() => {
    if (!bridge || !status) return;
    bridge.smoke.enabled().then((on) => { if (on) bridge!.smoke.rendered("app"); });
  }, [!!status]);

  // горячие клавиши: пробел — пауза, ←/→ — перемотка, Ctrl+→/← — следующий/предыдущий
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (e.ctrlKey && e.code === "KeyM") { e.preventDefault(); setMode(document.documentElement.dataset.mode === "compact" ? "full" : "compact"); return; }
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.code === "Space") { e.preventDefault(); player.toggle(); }
      else if (e.code === "ArrowRight") { e.preventDefault(); if (e.ctrlKey) player.next(); else player.seek(player.state.position + 10); }
      else if (e.code === "ArrowLeft") { e.preventDefault(); if (e.ctrlKey) player.prev(); else player.seek(player.state.position - 10); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!backendState.ready) return <div className="window"><TitleBar /><Boot error={backendState.error} /></div>;
  if (mode === "compact") return <AppCtx.Provider value={ctx}><CompactPlayer /></AppCtx.Provider>;

  const needAuth = status && status.auth === "none";
  return (
    <AppCtx.Provider value={ctx}>
      <div className="window">
        <TitleBar />
        <div className="shell">
          <Sidebar />
          <main className="content">
            {needAuth && route.view !== "settings" ? <SignIn /> : (
              <ViewBoundary key={`${route.view}:${route.id ?? ""}`}><div className="view">
                {route.view === "listen" && <ListenView />}
                {route.view === "library" && <LibraryView />}
                {route.view === "artist" && route.id !== undefined && <ArtistView id={route.id} />}
                {route.view === "album" && route.id !== undefined && <AlbumView id={route.id} />}
                {route.view === "history" && <HistoryView />}
                {route.view === "catalog" && <CatalogView />}
                {route.view === "settings" && <SettingsView />}
              </div></ViewBoundary>
            )}
          </main>
        </div>
        <PlayerBar />
      </div>
    </AppCtx.Provider>
  );
}
