// Связь с локальным сервисом. В Electron адрес и ключ приходят из preload,
// в тестовом браузере — из параметров адреса (?api=...&t=...).

export type Track = {
  id: string; title: string; version?: string | null; artists: string; artist_id: number | null;
  album_id: number | null; album?: string | null; year?: number | null; genre?: string; duration_ms?: number | null;
  catalog?: string; cover?: string | null; stream?: string; codec?: string; bitrate?: number; error?: string;
};
export type Artist = { id: number; name: string; tracks: number; catalogs: string[] };
export type Album = { id: number; title: string; year?: number; type?: string; genre?: string; artist_id?: number; artists?: string; tracks: number; cover_track?: string | null };
export type GenreInfo = { key: string; id: string; title: string; parent: string; general: boolean; artists: number; tracks: number };
export type GenreGroup = { id: string; title: string; keys: string[]; artists: number; tracks: number };
export type Pool = { artists: number; tracks: number; allowed_artists: number; allowed_tracks: number; blocked_artists: number };
export type ExtendResult = {
  added: string[]; message?: string; error?: string; backup?: string; queue?: number;
  before?: { tracks: number; artists: number; playable: number }; after?: { tracks: number; artists: number; playable: number };
};
export type Catalog = {
  name: string; path: string; size_mb: number; roots: string[]; scope: string[]; tracks: number; artists: number;
  discographies: number; queue: number; albums: number; runs: number; genres: GenreInfo[]; error?: string;
  candidates: number; playable_artists: number; groups: GenreGroup[];
};
export type Settings = {
  genres: string[]; volume: number; artist_cooldown: number; track_cooldown_days: number;
  theme: "auto" | "light" | "dark"; history_months: number;
  country_mode: "all" | "include" | "exclude"; country_list: string[]; country_unknown: boolean;
};
export type CountryStatus = {
  running: boolean; phase?: "yandex" | "musicbrainz"; auto?: boolean; done?: number; todo?: number; found?: number;
  found_yandex?: number; found_mb?: number; errors?: number; eta_s?: number; current?: string | null; message?: string | null;
  artists: number; checked: number; known: number; unknown: number; known_pct: number | null; known_of_checked_pct: number | null;
  statuses: Record<string, number>; by_source: Record<string, number>; countries: [string, number][];
  pending: number; pending_yandex: number; pending_mb: number;
  filter: { artists: number; blocked: number; allowed: number };
};
export type Status = {
  auth: "ok" | "none" | "offline" | "connecting"; account?: { name?: string; login?: string; has_plus?: boolean } | null;
  conn_error?: string | null; root: string; catalogs: number; tracks: number; artists: number; crawl_running: boolean;
  stats: { plays: number; artists: number; hours: number }; python: string;
};

export type WinMode = "full" | "compact";
export type CacheInfo = { httpBytes: number; httpLimit: number; logBytes: number };
export type Storage = {
  history_rows: number; history_oldest: number | null; history_months: number; covers_cached: number; state_db_bytes: number;
  stats: { plays: number; artists: number; hours: number; tracks: number };
  last_maintenance: { at: number; history_removed: number; covers: number } | null;
};
type Bridge = {
  backend: () => Promise<{ url: string; token: string; error: string; logPath: string }>;
  restartBackend: () => Promise<{ url: string; token: string; error: string; logPath: string }>;
  onBackend: (cb: (i: { url: string; token: string; error: string; logPath: string }) => void) => void;
  win: {
    minimize: () => void; toggleMaximize: () => void; close: () => void; isMaximized: () => Promise<boolean>; onMaximized: (cb: (m: boolean) => void) => void;
    getMode: () => Promise<{ mode: WinMode; pinned: boolean }>; setMode: (m: WinMode) => void; onMode: (cb: (m: WinMode) => void) => void;
    setPinned: (p: boolean) => Promise<boolean>;
  };
  smoke: { enabled: () => Promise<boolean>; rendered: (what: string) => void };
  cache: { info: () => Promise<CacheInfo>; clear: () => Promise<CacheInfo> };
  openExternal: (url: string) => Promise<void>;
  showLog: () => Promise<void>;
  platform: string;
};

declare global { interface Window { musicApp?: Bridge } }

export const bridge: Bridge | undefined = window.musicApp;
let base = "";
let token = "";
const listeners = new Set<() => void>();
export const backendState = { ready: false, error: "" };

export async function initBackend(): Promise<void> {
  const apply = (i: { url: string; token: string; error: string }) => {
    base = i.url; token = i.token; backendState.ready = !!i.url; backendState.error = i.error;
    listeners.forEach((l) => l());
  };
  if (bridge) {
    bridge.onBackend(apply);
    apply(await bridge.backend());
  } else {
    const p = new URLSearchParams(location.search);
    apply({ url: p.get("api") || "", token: p.get("t") || "", error: p.get("api") ? "" : "нет адреса сервиса" });
  }
}
export const onBackendChange = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };

async function call<T>(method: "GET" | "POST", path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  if (!base) throw new Error("сервис ещё запускается");
  const r = await fetch(base + path, {
    method, signal,
    headers: { "X-Token": token, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data as { error?: string }).error || `HTTP ${r.status}`);
  return data as T;
}
export const api = {
  get: <T>(p: string, signal?: AbortSignal) => call<T>("GET", p, undefined, signal),
  post: <T>(p: string, b: unknown = {}) => call<T>("POST", p, b),
  stream: (path: string) => `${base}${path}?t=${encodeURIComponent(token)}`,
};

// пакетная подгрузка обложек с кэшем
const coverCache = new Map<string, string | null>();
let pending = new Set<string>();
let timer: number | null = null;
const coverSubs = new Set<() => void>();
export function coverFor(trackId: string | undefined | null): string | null | undefined {
  if (!trackId) return null;
  if (coverCache.has(trackId)) return coverCache.get(trackId);
  pending.add(trackId);
  if (timer === null) timer = window.setTimeout(flushCovers, 60);
  return undefined;
}
async function flushCovers() {
  timer = null;
  const ids = [...pending].slice(0, 100);
  pending = new Set([...pending].slice(100));
  if (pending.size) timer = window.setTimeout(flushCovers, 200);
  try {
    const res = await api.post<Record<string, string>>("/api/covers", { ids });
    ids.forEach((id) => coverCache.set(id, res[id] ?? null));
  } catch { ids.forEach((id) => coverCache.delete(id)); }
  coverSubs.forEach((s) => s());
}
export const onCovers = (s: () => void) => { coverSubs.add(s); return () => { coverSubs.delete(s); }; };

// фото исполнителей (тем же способом, пакетами)
const artistCache = new Map<number, string | null>();
let artistPending = new Set<number>();
let artistTimer: number | null = null;
export function artistImage(id: number | null | undefined): string | null | undefined {
  if (!id) return null;
  if (artistCache.has(id)) return artistCache.get(id);
  artistPending.add(id);
  if (artistTimer === null) artistTimer = window.setTimeout(flushArtists, 80);
  return undefined;
}
async function flushArtists() {
  artistTimer = null;
  const ids = [...artistPending].slice(0, 100);
  artistPending = new Set([...artistPending].slice(100));
  if (artistPending.size) artistTimer = window.setTimeout(flushArtists, 250);
  try {
    const res = await api.post<Record<string, string>>("/api/artist-images", { ids });
    ids.forEach((id) => artistCache.set(id, res[String(id)] ?? null));
  } catch { ids.forEach((id) => artistCache.delete(id)); }
  coverSubs.forEach((s) => s());
}
export const rememberCover = (id: string, url?: string | null) => { if (url) coverCache.set(id, url); };

export const fmtTime = (s: number) => {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60), r = Math.floor(s % 60);
  return `${m}:${r.toString().padStart(2, "0")}`;
};
export const plural = (n: number, one: string, few: string, many: string) => {
  const a = Math.abs(n) % 100, b = a % 10;
  return a > 10 && a < 20 ? many : b > 1 && b < 5 ? few : b === 1 ? one : many;
};
export const num = (n: number) => n.toLocaleString("ru-RU");
