import { num, plural } from "../../api";
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
  const sel = new Set(settings?.genres ?? []);
  const all = catalogs.flatMap((c) => c.genres);
  const chosen = all.filter((g) => sel.has(g.key));
  const eff = chosen.length ? chosen : all;
  const artists = eff.reduce((a, g) => a + g.artists, 0), tracks = eff.reduce((a, g) => a + g.tracks, 0);
  const toggle = (k: string) => { const s = new Set(sel); if (s.has(k)) s.delete(k); else s.add(k); saveSettings({ genres: [...s] }); };
  const start = () => player.startRadio(settings?.genres ?? []);
  if (!catalogs.length) return <Empty title="Каталог пуст">Откройте раздел «Каталог» и запустите сбор.</Empty>;
  return (
    <section className="card genres">
      <div className="card-head">
        <div>
          <h2>Жанры</h2>
          <p className="muted">{chosen.length ? `Выбрано: ${chosen.length}` : "Не выбрано — звучат все жанры"} · {chosen.length > 1 ? "≈" : ""}{num(artists)} {plural(artists, "исполнитель", "исполнителя", "исполнителей")}, {num(tracks)} {plural(tracks, "трек", "трека", "треков")}</p>
        </div>
        <div className="row gap">
          {chosen.length > 0 && <button className="btn ghost" onClick={() => saveSettings({ genres: [] })}>Сбросить</button>}
          <button className="btn primary" onClick={start} disabled={st.starting}><IShuffle size={16} />{st.current ? "Новая подборка" : "Слушать подборку"}</button>
        </div>
      </div>
      {catalogs.map((c) => (
        <div key={c.name} className="genre-group">
          {catalogs.length > 1 && <div className="group-label">{c.name}</div>}
          <div className="chips">
            {c.genres.map((g) => (
              <button key={g.key} className={`chip ${sel.has(g.key) ? "on" : ""}`} onClick={() => toggle(g.key)}
                title={`${num(g.artists)} исполнителей · ${num(g.tracks)} треков`}>
                {g.title}<span>{num(g.artists)}</span>
              </button>
            ))}
          </div>
        </div>
      ))}
      <p className="fine">Сначала случайно выбирается исполнитель, затем его трек: известность и размер дискографии не дают преимущества. Недавние исполнители и треки не повторяются.</p>
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
