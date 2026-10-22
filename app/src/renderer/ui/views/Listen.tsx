import { useEffect, useState } from "react";
import { api, num, Pool, plural } from "../../api";
import { player } from "../../player";
import { useApp } from "../App";
import { Cover, Empty, TrackRow, usePlayer, useCover } from "../common";
import { IPause, IPlay, IShuffle, INext } from "../icons";

function NowPlaying() {
  const st = usePlayer();
  const { go, genreTitle } = useApp();
  const t = st.current;
  const cover = useCover(t?.id, t?.cover);
  if (!t) return null;
  const up = st.upcoming;
  return (
    <section className="hero">
      {cover && <div className="hero-bg" style={{ backgroundImage: `url("${cover}")` }} />}
      <div className="hero-cover"><Cover trackId={t.id} src={t.cover} size={232} radius={14} title={t.album || t.title} /></div>
      <div className="hero-meta">
        <div className="eyebrow">{st.modeLabel || "Сейчас играет"}{t.genre ? ` · ${genreTitle(`${t.catalog}:${t.genre}`)}` : ""}</div>
        <h1 className="hero-title">{t.title}{t.version ? <span className="muted"> ({t.version})</span> : null}</h1>
        <div className="hero-artist">{t.artist_id ? <a onClick={() => go({ view: "artist", id: t.artist_id! })}>{t.artists}</a> : t.artists}</div>
        <div className="hero-album">
          {t.album_id ? <a onClick={() => go({ view: "album", id: t.album_id! })}>{t.album}</a> : t.album}
          {t.year ? ` · ${t.year}` : ""}{t.codec ? <span className="badge">{t.codec.toUpperCase()} {t.bitrate}</span> : null}
        </div>
        <div className="row gap hero-actions">
          <button className="btn primary round" onClick={() => player.toggle()}>{st.playing ? <><IPause size={16} />Пауза</> : <><IPlay size={16} />Слушать</>}</button>
          <button className="btn round" onClick={() => player.next()}><INext size={15} />Дальше</button>
        </div>
        {up && <div className="upnext"><span className="muted">Далее:</span> {up.artists} — {up.title}</div>}
      </div>
    </section>
  );
}

function GenrePicker() {
  const { catalogs, settings, saveSettings } = useApp();
  const st = usePlayer();
  const [pool, setPool] = useState<Pool | null>(null);
  const selected = settings?.genres ?? [];
  const sel = new Set(selected);
  const known = new Set(catalogs.flatMap((c) => c.genres.map((g) => g.key)));
  const chosen = selected.filter((k) => known.has(k));
  const cmode = settings?.country_mode ?? "all";
  // уникальные исполнители выбранных жанров с учётом фильтра стран — считает сервис
  useEffect(() => {
    let alive = true;
    api.post<Pool>("/api/pool", { genres: chosen }).then((r) => alive && setPool(r)).catch(() => {});
    return () => { alive = false; };
  }, [chosen.join("|"), catalogs, cmode, (settings?.country_list ?? []).join(","), settings?.country_unknown]);
  const save = (s: Set<string>) => saveSettings({ genres: [...s] });
  const toggle = (k: string) => { const s = new Set(sel); if (s.has(k)) s.delete(k); else s.add(k); save(s); };
  const toggleGroup = (keys: string[]) => {
    const s = new Set(sel);
    const all = keys.every((k) => s.has(k));
    keys.forEach((k) => (all ? s.delete(k) : s.add(k)));
    save(s);
  };
  const start = () => player.startRadio(settings?.genres ?? []);
  if (!catalogs.length) return <Empty title="Каталог пуст">Откройте раздел «Каталог» и запустите сбор.</Empty>;
  const filtered = pool && pool.blocked_artists > 0;
  return (
    <section className="card genres">
      <div className="card-head">
        <div>
          <h2>Жанры</h2>
          <p className="muted">{chosen.length ? `Выбрано жанров: ${chosen.length}` : "Не выбрано — звучат все жанры"}
            {pool && <> · <b>{num(pool.allowed_artists)}</b> {plural(pool.allowed_artists, "исполнитель", "исполнителя", "исполнителей")}, {num(pool.allowed_tracks)} {plural(pool.allowed_tracks, "трек", "трека", "треков")}</>}
            {filtered && <span title="Активен фильтр стран (Настройки → «Страны исполнителей»)"> · без {num(pool!.blocked_artists)} по фильтру стран</span>}</p>
        </div>
        <div className="row gap">
          {chosen.length > 0 && <button className="btn ghost" onClick={() => saveSettings({ genres: [] })}>Сбросить</button>}
          <button className="btn primary" onClick={start} disabled={st.starting || (pool !== null && pool.allowed_artists === 0)}><IShuffle size={16} />{st.current ? "Новая подборка" : "Слушать подборку"}</button>
        </div>
      </div>
      {pool && pool.allowed_artists === 0 && pool.artists > 0 && <div className="note">Фильтр стран исключил всех исполнителей выбранных жанров — измените его в настройках.</div>}
      {catalogs.map((c) => (
        <div key={c.name} className="genre-catalog">
          {catalogs.length > 1 && <div className="group-label">{c.name}</div>}
          {(c.groups?.length ? c.groups : [{ id: c.name, title: c.name, keys: c.genres.map((g) => g.key), artists: 0, tracks: 0 }]).map((grp) => {
            const gs = grp.keys.map((k) => c.genres.find((g) => g.key === k)!).filter(Boolean);
            const on = gs.filter((g) => sel.has(g.key)).length;
            const state = on === 0 ? "" : on === gs.length ? "on" : "partial";
            return (
              <div key={grp.id} className="genre-group">
                <button className={`group-chip ${state}`} onClick={() => toggleGroup(grp.keys)}
                  title={state === "on" ? "Снять всю группу" : "Выбрать всю группу"}>
                  <i className="check" />{grp.title}<span>{num(grp.artists)}</span>
                </button>
                <div className="chips">
                  {gs.map((g) => (
                    <button key={g.key} className={`chip ${sel.has(g.key) ? "on" : ""}`} onClick={() => toggle(g.key)}
                      title={`${num(g.artists)} исполнителей · ${num(g.tracks)} треков${g.general ? " · альбомы, отмеченные только общим жанром" : ""}`}>
                      {g.general && gs.length > 1 ? `${g.title}: общее` : g.title}<span>{num(g.artists)}</span>
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      ))}
      <p className="fine">Кнопка группы выбирает её целиком, отдельные жанры — по одному. Числа — уникальные исполнители, у которых есть треки этого жанра.
        Сначала случайно выбирается исполнитель, затем его трек: известность и размер дискографии не дают преимущества. Недавние исполнители и треки не повторяются.</p>
    </section>
  );
}

export function ListenView() {
  const st = usePlayer();
  const { go, settings } = useApp();
  const session = st.session.slice(st.current ? 1 : 0, 40);
  return (
    <div className="listen">
      <NowPlaying />
      <GenrePicker />
      {session.length > 0 && (
        <section className="card">
          <div className="card-head"><h2>В этой сессии</h2></div>
          <div className="tlist">
            {session.map((t, i) => <TrackRow key={`${t.id}-${i}`} t={t} onPlay={() => player.playQueue([t], 0, "Повтор", settings?.genres ?? [])}
              onArtist={(id) => go({ view: "artist", id })} />)}
          </div>
        </section>
      )}
    </div>
  );
}
