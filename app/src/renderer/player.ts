// Аудиодвижок: два <audio> (текущий и заранее загруженный следующий), радио по жанрам и очереди,
// собственная история, восстановление после ошибок, обрывов сети и долгих пауз.
import { api, Track, rememberCover } from "./api";

type Mode =
  | { kind: "radio"; genres: string[] }
  | { kind: "queue"; items: Track[]; pos: number; label: string; genres: string[] };

export type PlayerState = {
  current: Track | null; upcoming: Track | null; playing: boolean; position: number; duration: number;
  buffering: boolean; volume: number; error: string; modeLabel: string; session: Track[]; canPrev: boolean;
  starting: boolean;
};

const MAX_ERRORS = 6;

/** Ждём возвращения сети или истечения паузы — что раньше. */
const waitOnline = (ms: number) => new Promise<void>((resolve) => {
  const done = () => { clearTimeout(t); window.removeEventListener("online", done); resolve(); };
  const t = window.setTimeout(done, navigator.onLine ? ms : Math.max(ms, 60000));
  window.addEventListener("online", done);
});

class Player {
  private els: HTMLAudioElement[] = [new Audio(), new Audio()];
  private cur = 0;
  private mode: Mode = { kind: "radio", genres: [] };
  private back: Track[] = [];
  private forward: Track[] = [];
  private gen = 0;
  private upcomingFor = -1;
  /** Письменности названий, исключённые из случайного выбора (Настройки). Проверяется и здесь, чтобы уже
   *  подготовленный трек не успел зазвучать до ответа сервиса на смену настроек. */
  private excludedScripts = new Set<string>();
  private upcomingReady = false;
  private playId: number | null = null;
  private listened = 0;
  private lastTime = 0;
  private errors = 0;
  private stallTimer: number | null = null;
  private subs = new Set<() => void>();
  private snap: PlayerState;
  state: PlayerState = {
    current: null, upcoming: null, playing: false, position: 0, duration: 0, buffering: false, volume: 0.8,
    error: "", modeLabel: "", session: [], canPrev: false, starting: false,
  };

  constructor() {
    this.snap = { ...this.state };
    this.els.forEach((el, i) => {
      el.preload = "auto";
      el.addEventListener("timeupdate", () => { if (i === this.cur) this.onTime(el); });
      el.addEventListener("durationchange", () => { if (i === this.cur) this.set({ duration: el.duration || 0 }); });
      el.addEventListener("playing", () => { if (i === this.cur) { this.clearStall(); this.set({ playing: true, buffering: false, error: "" }); } });
      el.addEventListener("pause", () => { if (i === this.cur && !el.ended) this.set({ playing: false }); });
      el.addEventListener("waiting", () => { if (i === this.cur) { this.set({ buffering: true }); this.armStall(); } });
      el.addEventListener("stalled", () => { if (i === this.cur) this.armStall(); });
      el.addEventListener("ended", () => { if (i === this.cur) this.finishAndAdvance("finished"); });
      el.addEventListener("error", () => { if (i === this.cur && el.getAttribute("src")) this.onError(`ошибка аудио (${el.error?.code ?? "?"})`); });
    });
    window.addEventListener("online", () => { if (this.state.current && this.state.buffering) this.reloadCurrent(); });
    window.addEventListener("offline", () => this.set({ error: "Нет подключения к сети — воспроизведение продолжится, когда сеть вернётся" }));
    const ms = navigator.mediaSession;
    if (ms) {
      ms.setActionHandler("play", () => this.toggle(true));
      ms.setActionHandler("pause", () => this.toggle(false));
      ms.setActionHandler("nexttrack", () => this.next());
      ms.setActionHandler("previoustrack", () => this.prev());
      ms.setActionHandler("seekto", (d) => d.seekTime !== undefined && this.seek(d.seekTime));
    }
  }

  // ---------------------------------------------------------------- подписка (React)
  subscribe = (fn: () => void) => { this.subs.add(fn); return () => { this.subs.delete(fn); }; };
  getSnapshot = () => this.snap;
  private set(p: Partial<PlayerState>) {
    this.state = { ...this.state, ...p };
    this.snap = this.state;
    this.subs.forEach((s) => s());
  }
  private get el() { return this.els[this.cur]; }
  private get idle() { return this.els[1 - this.cur]; }

  // ---------------------------------------------------------------- публичные действия
  setVolume(v: number) {
    v = Math.min(1, Math.max(0, v));
    this.els.forEach((e) => (e.volume = v));
    this.set({ volume: v });
  }

  async startRadio(genres: string[]) {
    this.endPlay("skipped");
    this.mode = { kind: "radio", genres };
    this.forward = [];
    this.set({ modeLabel: "Случайная подборка", error: "" });
    await this.advance();
  }

  async playQueue(items: Track[], start: number, label: string, genres: string[]) {
    if (!items.length) return;
    this.endPlay("skipped");
    this.mode = { kind: "queue", items, pos: start, label, genres };
    this.forward = [];
    this.set({ modeLabel: label, error: "" });
    await this.playTrack(items[start]);
  }

  toggle(force?: boolean) {
    const el = this.el;
    if (!this.state.current) return;
    const play = force ?? el.paused;
    if (play) el.play().catch((e) => this.onError(String(e?.message || e)));
    else el.pause();
  }

  seek(t: number) {
    const el = this.el;
    if (!this.state.current || !isFinite(t)) return;
    el.currentTime = Math.max(0, Math.min(t, (el.duration || t) - 0.25));
    this.lastTime = el.currentTime;
    this.set({ position: el.currentTime });
  }

  async next() { await this.finishAndAdvance("skipped"); }

  async prev() {
    if (this.el.currentTime > 4 || !this.back.length) { this.seek(0); return; }
    const t = this.back.pop()!;
    if (this.state.current) this.forward.push(this.state.current);
    this.endPlay("skipped");
    await this.playTrack(t, false);
  }

  // ---------------------------------------------------------------- внутренняя логика
  private async finishAndAdvance(status: "finished" | "skipped"): Promise<void> {
    this.endPlay(status);
    await this.advance();
  }

  private async advance(): Promise<void> {
    const gen = ++this.gen;
    if (this.upcomingReady && this.state.upcoming && this.upcomingFor === gen - 1 &&
        (this.mode.kind !== "radio" || this.allowedRandom(this.state.upcoming))) {
      const t = this.state.upcoming;
      this.swapToIdle();
      return this.startOn(t, gen, true);
    }
    this.set({ starting: true });
    const t = await this.fetchUpcoming(gen);
    if (gen !== this.gen) return;
    if (!t) { this.set({ starting: false, playing: false }); return; }
    return this.playTrack(t, true, gen);
  }

  private async playTrack(t: Track, pushBack = true, gen = ++this.gen): Promise<void> {
    if (gen !== this.gen) return;
    if (this.state.current && pushBack && this.state.current.id !== t.id) this.back.push(this.state.current);
    if (this.back.length > 200) this.back.shift();
    this.set({ starting: true });
    let ready = t;
    if (!t.stream) {
      try { ready = await api.get<Track>(`/api/track/${t.id}`); }
      catch (e) { return this.onError(String((e as Error).message)); }
      if (gen !== this.gen) return;
      if (ready.error) { this.errors++; return this.errors < MAX_ERRORS ? this.advance() : this.stopWith("Подряд несколько недоступных треков"); }
    }
    this.releaseIdle();
    this.swapToIdle();
    this.el.src = api.stream(ready.stream!);
    return this.startOn(ready, gen, false);
  }

  private async startOn(t: Track, gen: number, preloaded: boolean): Promise<void> {
    rememberCover(t.id, t.cover);
    if (preloaded && this.state.current && this.state.current.id !== t.id) this.back.push(this.state.current);
    this.releaseIdle();
    this.listened = 0;
    this.lastTime = 0;
    this.upcomingReady = false;
    this.set({ current: t, upcoming: null, position: 0, duration: (t.duration_ms || 0) / 1000, buffering: true,
               starting: false, session: [t, ...this.state.session].slice(0, 300), canPrev: this.back.length > 0 });
    this.setMeta(t);
    try {
      await this.el.play();
    } catch (e) {
      if (gen !== this.gen) return;
      return this.onError(String((e as Error)?.message || e));
    }
    if (gen !== this.gen) return;
    this.errors = 0;
    api.post<{ play_id: number }>("/api/history/start", { track_id: t.id, origin: this.mode.kind })
      .then((r) => {
        if (gen === this.gen) this.playId = r.play_id;
        else api.post("/api/history/end", { play_id: r.play_id, listened_s: Math.round(this.listened), status: "skipped" }).catch(() => {});
      }).catch(() => {});
    this.prepareUpcoming(gen);
  }

  setScriptFilter(ids: string[]) { this.excludedScripts = new Set(ids); }

  private allowedRandom(t: Track | null | undefined): boolean {
    return !t || !this.excludedScripts.size || !(t.scripts ?? []).some((s) => this.excludedScripts.has(s));
  }

  /** Настройки отбора изменились (например, фильтр стран): заранее выбранный случайный трек выбрать заново. */
  refreshUpcoming() {
    if (this.mode.kind !== "radio" || this.forward.length || !this.state.current) return;
    this.releaseIdle();
    this.set({ upcoming: null });
    this.prepareUpcoming(this.gen);
  }

  private upTok = 0;

  private async prepareUpcoming(gen: number) {
    const tok = ++this.upTok;
    const t = await this.fetchUpcoming(gen);
    if (gen !== this.gen || !t || tok !== this.upTok) return;
    rememberCover(t.id, t.cover);
    const idle = this.idle;
    idle.src = api.stream(t.stream!);
    idle.load();
    this.upcomingFor = gen;
    this.upcomingReady = true;
    this.set({ upcoming: t });
  }

  /**
   * Следующий трек. Сетевые сбои (нет интернета, Яндекс недоступен) не останавливают музыку:
   * попытка повторяется с нарастающей паузой и сразу после возвращения сети.
   * Состояние очереди меняется только если за это время не началось другое воспроизведение (gen).
   */
  private async fetchUpcoming(gen: number): Promise<Track | null> {
    for (let attempt = 0; attempt < 60; attempt++) {
      if (gen !== this.gen) return null;
      const r = await this.tryUpcoming(gen);
      if (gen !== this.gen) return null;
      if ("track" in r) {
        if (this.state.error.startsWith("Нет связи")) this.set({ error: "" });
        return r.track;
      }
      if ("fatal" in r) { this.set({ error: r.fatal }); return null; }
      this.set({ error: `Нет связи с Яндексом — повторяю попытку… (${r.retry})` });
      await waitOnline(Math.min(30000, 2000 * 2 ** Math.min(attempt, 4)));
    }
    return null;
  }

  private async tryUpcoming(gen: number): Promise<{ track: Track } | { fatal: string } | { retry: string }> {
    try {
      // в случайном режиме трек «вперёд по истории» с исключённой письменностью пропускается
      while (this.mode.kind === "radio" && this.forward.length && !this.allowedRandom(this.forward[this.forward.length - 1])) this.forward.pop();
      if (this.forward.length) {
        const f = this.forward[this.forward.length - 1];
        const t = f.stream ? f : await api.get<Track>(`/api/track/${f.id}`);
        if (gen !== this.gen) return { retry: "" };
        this.forward.pop();
        if (!t.error) return { track: t };
      }
      if (this.mode.kind === "queue") {
        const m = this.mode;
        let idx = m.pos + 1;
        while (idx < m.items.length) {
          const t = await api.get<Track>(`/api/track/${m.items[idx].id}`);
          if (gen !== this.gen) return { retry: "" };
          if (!t.error) { m.pos = idx; return { track: t }; }
          idx += 1;                                          // недоступный трек очереди пропускаем
        }
        m.pos = idx;
        this.mode = { kind: "radio", genres: m.genres };     // очередь кончилась — дальше случайно
        this.set({ modeLabel: "Случайная подборка" });
      }
      const exclude = [this.state.current?.id, ...this.state.session.slice(0, 30).map((s) => s.id)].filter(Boolean);
      let t = await api.post<Track & { retry?: boolean }>("/api/next", {
        genres: this.mode.genres, exclude, exclude_artists: this.state.current?.artist_id ? [this.state.current.artist_id] : [],
      });
      // ответ на запрос, отправленный до смены фильтра письменности, — выбрать заново
      for (let i = 0; i < 4 && !t.error && !this.allowedRandom(t); i++) {
        if (gen !== this.gen) return { retry: "" };
        t = await api.post<Track & { retry?: boolean }>("/api/next", { genres: this.mode.genres, exclude: [...exclude, t.id] });
      }
      if (!t.error && !this.allowedRandom(t)) return { retry: "" };
      if (!t.error) return { track: t };
      if (t.error === "auth") return { fatal: "Войдите в Яндекс, чтобы слушать" };
      return t.retry ? { retry: t.error } : { fatal: t.error };
    } catch (e) {
      return { retry: (e as Error).message };
    }
  }

  private swapToIdle() { this.cur = 1 - this.cur; }

  private releaseIdle() {
    const idle = this.idle;
    idle.pause();
    if (idle.getAttribute("src")) { idle.removeAttribute("src"); idle.load(); }
    this.upcomingReady = false;
  }

  private endPlay(status: string) {
    if (this.playId !== null) {
      api.post("/api/history/end", { play_id: this.playId, listened_s: Math.round(this.listened), status }).catch(() => {});
      this.playId = null;
    }
  }

  /** При закрытии окна: записать, сколько прослушан текущий трек. */
  flushOnExit() {
    if (this.playId === null) return;
    const body = JSON.stringify({ play_id: this.playId, listened_s: Math.round(this.listened), status: "stopped" });
    navigator.sendBeacon?.(api.stream("/api/history/end"), new Blob([body], { type: "text/plain" }));
    this.playId = null;
  }

  private onTime(el: HTMLAudioElement) {
    const t = el.currentTime;
    const d = t - this.lastTime;
    if (d > 0 && d < 2) this.listened += d;
    this.lastTime = t;
    this.set({ position: t });
    if (navigator.mediaSession && el.duration && isFinite(el.duration)) {
      try { navigator.mediaSession.setPositionState({ duration: el.duration, position: Math.min(t, el.duration), playbackRate: 1 }); } catch { /* */ }
    }
  }

  private onError(msg: string) {
    this.clearStall();
    this.endPlay("error");
    this.errors++;
    if (this.errors >= MAX_ERRORS) return this.stopWith(`Воспроизведение остановлено: ${msg}`);
    this.set({ error: `Трек пропущен: ${msg}` });
    window.setTimeout(() => this.advance(), 400);
  }

  private stopWith(msg: string) {
    this.el.pause();
    this.set({ playing: false, starting: false, error: msg });
    this.errors = 0;
  }

  // долгая буферизация — переподключаемся с той же позиции (прокси возьмёт свежую ссылку)
  private armStall() {
    if (this.stallTimer !== null) return;
    this.stallTimer = window.setTimeout(() => { this.stallTimer = null; if (this.state.buffering && navigator.onLine) this.reloadCurrent(); }, 12000);
  }
  private clearStall() { if (this.stallTimer !== null) { clearTimeout(this.stallTimer); this.stallTimer = null; } }

  private reloadCurrent() {
    const t = this.state.current;
    if (!t?.stream) return;
    const el = this.el, pos = el.currentTime, wasPlaying = !el.paused || this.state.buffering;
    el.src = api.stream(t.stream) + `&r=${Date.now()}`;
    el.addEventListener("loadedmetadata", () => { el.currentTime = pos; if (wasPlaying) el.play().catch(() => {}); }, { once: true });
  }

  private setMeta(t: Track) {
    document.title = `${t.title} — ${t.artists}`;
    if (!navigator.mediaSession) return;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: t.title, artist: t.artists, album: t.album || "", artwork: t.cover ? [{ src: t.cover, sizes: "400x400", type: "image/jpeg" }] : [],
    });
  }
}

export const player = new Player();
window.addEventListener("pagehide", () => player.flushOnExit());
