import { useEffect, useMemo, useState } from "react";
import { api, CountryStatus, num, Settings } from "../../api";
import { Segmented, Toggle } from "../common";

const SOURCES: Record<string, string> = { yandex: "Яндекс Музыка", musicbrainz: "MusicBrainz" };
const YA_S = 0.7, MB_S = 4.6;               // секунд на исполнителя по пробной выборке
const key = (s: string) => s.toLocaleLowerCase("ru").replace(/ё/g, "е");

function eta(s?: number) {
  if (!s || s < 60) return "меньше минуты";
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h} ч ${m} мин` : `${m} мин`;
}

function Row({ title, hint, children }: { title: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return <div className="setting"><div><div className="setting-title">{title}</div>{hint && <div className="setting-hint">{hint}</div>}</div><div className="setting-ctl">{children}</div></div>;
}

export function CountrySection({ settings, save, online }: { settings: Settings; save: (p: Partial<Settings>) => void; online: boolean }) {
  const [c, setC] = useState<CountryStatus | null>(null);
  const [names, setNames] = useState<[string, string][]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    api.get<{ names: [string, string][] }>("/api/countries/names").then((r) => alive && setNames(r.names)).catch(() => {});
    const poll = () => api.get<CountryStatus>("/api/countries").then((r) => alive && setC(r)).catch(() => {});
    poll();
    const t = window.setInterval(poll, 2000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  // после изменения фильтра сразу обновить «проходят фильтр»
  useEffect(() => { api.get<CountryStatus>("/api/countries").then(setC).catch(() => {}); }, [settings.country_mode, settings.country_list, settings.country_unknown]);

  const nameOf = useMemo(() => new Map(names), [names]);
  const counts = useMemo(() => new Map(c?.countries ?? []), [c]);
  const chosen = new Set(settings.country_list);
  const list = useMemo(() => {
    const k = key(q.trim());
    const all = names.filter(([code, n]) => !k || key(n).includes(k) || code.toLowerCase() === k);
    return all.sort((a, b) => (counts.get(b[0]) ?? 0) - (counts.get(a[0]) ?? 0) || a[1].localeCompare(b[1], "ru"));
  }, [names, q, counts]);

  const toggle = (code: string) => {
    const next = new Set(chosen);
    if (next.has(code)) next.delete(code); else next.add(code);
    save({ country_list: [...next].sort() });
  };
  const run = async (body: object) => {
    setBusy(true);
    try { setC(await api.post<CountryStatus>("/api/countries/start", body)); } finally { setBusy(false); }
  };
  const stop = async () => { setC(await api.post<CountryStatus>("/api/countries/stop")); };

  const mode = settings.country_mode;
  const st = c?.statuses ?? {};
  const unresolved = (st.nomatch ?? 0) + (st.ambiguous ?? 0) + (st.unverified ?? 0) + (st.matched_no_country ?? 0);
  const mbLeft = (c?.pending_mb ?? 0);
  const pct = c && c.todo ? Math.min(100, (100 * (c.done ?? 0)) / c.todo) : 0;

  return (
    <section className="card">
      <h2>Страны исполнителей</h2>
      <Row title="Какую музыку воспроизводить" hint="Фильтр действует на все жанры: на случайную подборку и на продолжение после очереди. Страна исполнителя — не язык песни.">
        <Segmented value={mode} onChange={(v) => save({ country_mode: v })}
          options={[["all", "Все страны"], ["include", "Только выбранные"], ["exclude", "Исключить выбранные"]]} />
      </Row>

      {mode !== "all" && <>
        <div className="country-pick">
          <div className="chips">
            {settings.country_list.length === 0 && <span className="muted">{mode === "include" ? "Выберите страны ниже — пока подходят только исполнители с неизвестной страной (если они разрешены)." : "Выберите страны, которые нужно исключить."}</span>}
            {settings.country_list.map((code) => (
              <button key={code} className="chip on" onClick={() => toggle(code)} title="Убрать">{nameOf.get(code) ?? code}<span>✕</span></button>
            ))}
          </div>
          <div className="search"><input placeholder="Найти страну…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <div className="country-list">
            {list.map(([code, n]) => (
              <label key={code} className={`country-item ${chosen.has(code) ? "on" : ""}`}>
                <input type="checkbox" checked={chosen.has(code)} onChange={() => toggle(code)} />
                <span className="cn">{n}</span>
                <span className="cc">{counts.get(code) ? num(counts.get(code)!) : ""}</span>
              </label>
            ))}
            {list.length === 0 && <p className="muted">Ничего не найдено</p>}
          </div>
        </div>
        <Row title="Исполнители с неизвестной страной" hint={<>Страна неизвестна, если её нет ни у Яндекса, ни в MusicBrainz (или совпадение не подтвердилось){c ? <> — сейчас таких <b>{num(c.unknown)}</b> из {num(c.artists)}</> : null}. По языку, жанру или названию страна не угадывается.</>}>
          <span className="val wide">{settings.country_unknown ? "воспроизводить" : "не воспроизводить"}</span>
          <Toggle checked={settings.country_unknown} onChange={(v) => save({ country_unknown: v })} />
        </Row>
        {c && <p className={`filter-sum ${c.filter.allowed === 0 ? "warn" : ""}`}>
          Проходят фильтр <b>{num(c.filter.allowed)}</b> из {num(c.filter.artists)} исполнителей, исключено {num(c.filter.blocked)}.
          {c.filter.allowed === 0 && " Музыка играть не будет — измените выбор."}
        </p>}
      </>}

      {c && <div className="country-box">
        <p>Страна определена для <b>{num(c.known)}</b> из {num(c.artists)} исполнителей — <b>{c.known_pct ?? 0}%</b>
          {Object.keys(c.by_source).length > 0 && <span className="muted"> ({Object.entries(c.by_source).map(([s, n]) => `${SOURCES[s] ?? s}: ${num(n)}`).join(", ")})</span>}.</p>
        <p className="muted">
          Не проверены: {num(st.unchecked ?? 0)} · ждут MusicBrainz: {num(st.no_yandex ?? 0)} · не определены: {num(unresolved)}
          {st.error ? ` · ошибки связи: ${num(st.error)}` : ""}
        </p>
        {c.running ? <div className="crawl-progress country-run">
          <p>{c.phase === "musicbrainz" ? "MusicBrainz (не чаще 1 запроса в секунду)" : "Яндекс Музыка"}{c.auto ? " · новые исполнители" : ""}: {num(c.done ?? 0)} / {num(c.todo ?? 0)} · найдено стран: <b>{num(c.found ?? 0)}</b>
            {c.current ? <span className="muted"> · {c.current}</span> : null}</p>
          <div className="bar thin"><div style={{ width: `${pct}%` }} /></div>
          <p className="muted">Осталось примерно {eta(c.eta_s)}. Можно пользоваться приложением и слушать музыку; остановка ничего не теряет.</p>
          <button className="btn" onClick={stop}>Остановить</button>
        </div> : <div className="btns">
          {c.pending_yandex > 0 && <button className="btn primary" disabled={busy || !online} onClick={() => run({ musicbrainz: false })}>
            {c.checked === 0 ? "Определить страны" : "Проверить оставшихся"} по Яндексу ({num(c.pending_yandex)}, ≈{eta(c.pending_yandex * YA_S)})</button>}
          {mbLeft > 0 && <button className="btn" disabled={busy || !online} onClick={() => run({ musicbrainz: true })}>
            Дополнить через MusicBrainz ({num(mbLeft)}, ≈{eta(mbLeft * MB_S)})</button>}
          {unresolved > 0 && <button className="btn ghost" disabled={busy || !online} onClick={() => run({ recheck: true, musicbrainz: true })}>Перепроверить неопределённых ({num(unresolved)})</button>}
        </div>}
        {c.message && <p className="muted">{c.message}</p>}
        <p className="fine">Сначала берётся страна из карточки исполнителя в Яндекс Музыке, для остальных — из открытой базы MusicBrainz:
          совпасть должно имя, и хотя бы один альбом должен найтись у этого исполнителя в MusicBrainz, поэтому одноимённые группы не путаются.
          Результаты сохраняются; новые исполнители после пополнения каталога проверяются автоматически, если фильтр включён.
          {" "}У известных исполнителей страна обычно есть у Яндекса; у малоизвестных её часто нет нигде — такие исполнители остаются с неизвестной страной.</p>
      </div>}
    </section>
  );
}
