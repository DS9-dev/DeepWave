import { useEffect, useState } from "react";
import { api, Catalog, num } from "../../api";
import { useApp } from "../App";
import { Empty } from "../common";
import { ICatalog, IStop } from "../icons";

type CrawlStatus = {
  running: boolean; catalog?: string; requests?: number; max_requests?: number; new_artists?: number; new_tracks?: number;
  new_albums?: number; errors?: number; elapsed_s?: number; stopping?: boolean; reason?: string | null; log: { t: string; msg: string }[];
};
type GNode = { id: string; title: string; sub: GNode[] };

const LIMITS: [number, string][] = [[300, "300 запросов · ~5 мин"], [1500, "1 500 · ~20 мин"], [5000, "5 000 · ~1 ч"], [20000, "20 000 · ~3,5 ч"]];
const REASONS: Record<string, string> = {
  "лимит запросов": "достигнут лимит запросов", "очередь пуста": "все найденные исполнители обработаны",
  "остановлено пользователем (Ctrl+C)": "остановлено вами", "лимит времени": "достигнут лимит времени",
};

function Progress({ st }: { st: CrawlStatus }) {
  const pct = st.max_requests ? Math.min(100, (100 * (st.requests || 0)) / st.max_requests) : 0;
  const mins = Math.floor((st.elapsed_s || 0) / 60), secs = (st.elapsed_s || 0) % 60;
  return (
    <div className="crawl-progress">
      <div className="row between"><b>{st.stopping ? "Останавливаю — дописываю текущую задачу…" : "Идёт сбор"}</b>
        <span className="muted">{num(st.requests || 0)} / {num(st.max_requests || 0)} запросов · {mins}:{String(secs).padStart(2, "0")}</span></div>
      <div className="bar"><div style={{ width: `${pct}%` }} /></div>
      <div className="stats inline">
        <div><b>+{num(st.new_artists || 0)}</b>исполнителей</div><div><b>+{num(st.new_tracks || 0)}</b>треков</div>
        <div><b>+{num(st.new_albums || 0)}</b>альбомов</div><div><b>{st.errors || 0}</b>ошибок сети</div>
      </div>
    </div>
  );
}

function CatalogCard({ c, crawl, onStart, onStop }: { c: Catalog; crawl: CrawlStatus | null; onStart: (lim: number) => void; onStop: () => void }) {
  const { genreTitle } = useApp();
  const [lim, setLim] = useState(1500);
  const running = crawl?.running && crawl.catalog === c.name;
  const busyOther = crawl?.running && crawl.catalog !== c.name;
  const donePct = c.artists ? Math.round((100 * c.discographies) / c.artists) : 0;
  return (
    <section className="card catalog">
      <div className="card-head">
        <div className="row gap"><div className="cat-icon"><ICatalog size={20} /></div>
          <div><h2>{c.name}</h2><p className="muted small">{c.genres.slice(0, 8).map((g) => genreTitle(g.key)).join(", ")}{c.genres.length > 8 ? "…" : ""}</p></div></div>
        <span className="muted small">{c.size_mb} МБ · {c.runs} {c.runs === 1 ? "запуск" : "запусков"}</span>
      </div>
      <div className="stats">
        <div><b>{num(c.tracks)}</b>треков жанра</div>
        <div><b>{num(c.artists)}</b>исполнителей найдено</div>
        <div><b>{num(c.discographies)}</b>дискографий собрано</div>
        <div><b>{num(c.queue)}</b>в очереди</div>
      </div>
      <div className="bar thin" title={`Собрано ${donePct}% дискографий найденных исполнителей`}><div style={{ width: `${donePct}%` }} /></div>
      <p className="fine">Собрано {donePct}% дискографий. Пока очередь не пуста, каждый новый сбор добавляет исполнителей и треки, уже обработанное не повторяется.</p>
      {running ? <>
        <Progress st={crawl!} />
        <button className="btn danger" onClick={onStop} disabled={crawl!.stopping}><IStop size={14} />Остановить</button>
      </> : (
        <div className="row gap">
          <select value={lim} onChange={(e) => setLim(+e.target.value)}>{LIMITS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          <button className="btn primary" disabled={!!busyOther} onClick={() => onStart(lim)}>Продолжить сбор</button>
          {busyOther && <span className="muted small">Сейчас собирается «{crawl!.catalog}»</span>}
        </div>
      )}
    </section>
  );
}

function NewCatalog({ onCreate, disabled }: { onCreate: (name: string, genres: string[]) => void; disabled: boolean }) {
  const [tree, setTree] = useState<GNode[]>([]);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [name, setName] = useState("");
  useEffect(() => { api.get<{ tree: GNode[] }>("/api/genre-tree").then((r) => setTree(r.tree)).catch(() => {}); }, []);
  const toggle = (id: string) => { const s = new Set(sel); if (s.has(id)) s.delete(id); else s.add(id); setSel(s); if (!name) setName([...s].join("+")); };
  const music = tree.filter((g) => !/podcast|audiobook|fairytale|poem|children|fiction|literature|booksnot/i.test(g.id));
  return (
    <section className="card">
      <div className="card-head"><div><h2>Новый каталог</h2><p className="muted small">Жанры выбираются вместе со всеми поджанрами. Alternative, metal, punk и indie у Яндекса — отдельные жанры, не часть рока.</p></div></div>
      {!tree.length ? <p className="muted">Список жанров появится после входа в Яндекс.</p> : <>
        <div className="chips">{music.map((g) => <button key={g.id} className={`chip ${sel.has(g.id) ? "on" : ""}`} onClick={() => toggle(g.id)} title={g.sub.map((s) => s.title).join(", ")}>{g.title}{g.sub.length ? <span>+{g.sub.length}</span> : null}</button>)}</div>
        <div className="row gap">
          <input className="text" placeholder="Имя каталога (латиницей)" value={name} onChange={(e) => setName(e.target.value.replace(/[^\w+-]/g, ""))} />
          <button className="btn primary" disabled={disabled || !sel.size || !name} onClick={() => onCreate(name, [...sel])}>Создать и начать сбор</button>
        </div>
        <p className="fine">Первый запуск соберёт стартовые точки и первые дискографии; для 19–20 тысяч треков обычно нужно несколько запусков по 1 500–5 000 запросов.</p>
      </>}
    </section>
  );
}

export function CatalogView() {
  const { catalogs, refresh, status } = useApp();
  const [crawl, setCrawl] = useState<CrawlStatus | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    const poll = async () => { try { const s = await api.get<CrawlStatus>("/api/crawl"); if (alive) setCrawl(s); } catch { /* */ } };
    poll();
    const t = window.setInterval(poll, 1500);
    return () => { alive = false; clearInterval(t); };
  }, []);
  const start = async (catalog: string, genres: string[], max: number) => {
    setErr("");
    const r = await api.post<CrawlStatus & { error?: string }>("/api/crawl/start", { catalog, genres, max_requests: max });
    if (r.error) setErr(r.error); else setCrawl(r);
    refresh();
  };
  const stop = async () => setCrawl(await api.post<CrawlStatus>("/api/crawl/stop"));
  const lastReason = crawl && !crawl.running && crawl.reason ? (REASONS[crawl.reason] ?? crawl.reason) : "";
  return (
    <div className="catalogs">
      <div className="page-head"><h1>Каталог</h1>
        <p className="muted">Сбор идёт в фоне — можно слушать музыку. Остановка сохраняет прогресс, следующий запуск продолжит с того же места.</p></div>
      {status?.auth !== "ok" && <div className="note">Для сбора нужен вход в Яндекс (раздел «Настройки»).</div>}
      {err && <div className="note error">{err}</div>}
      {lastReason && <div className="note">Последний сбор завершён: {lastReason}. +{num(crawl!.new_artists || 0)} исполнителей, +{num(crawl!.new_tracks || 0)} треков.</div>}
      {!catalogs.length && <Empty title="Каталогов пока нет">Создайте первый каталог ниже.</Empty>}
      {catalogs.map((c) => <CatalogCard key={c.name} c={c} crawl={crawl} onStart={(lim) => start(c.name, c.roots, lim)} onStop={stop} />)}
      {crawl && crawl.log.length > 0 && <section className="card">
        <div className="card-head"><h2>Журнал сбора</h2></div>
        <pre className="log">{crawl.log.slice(-40).map((l) => `${l.t}  ${l.msg}`).join("\n")}</pre>
      </section>}
      <NewCatalog disabled={!!crawl?.running || status?.auth !== "ok"} onCreate={(n, g) => start(n, g, 1500)} />
    </div>
  );
}
