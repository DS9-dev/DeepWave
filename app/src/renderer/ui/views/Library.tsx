import { useEffect, useState } from "react";
import { Album, api, Artist, num, plural, Track } from "../../api";
import { player } from "../../player";
import { useApp } from "../App";
import { Avatar, Cover, Empty, Segmented, Spinner, TrackRow } from "../common";
import { ISearch } from "../icons";

type Res = { artists: Artist[]; albums: Album[]; tracks: Track[] };
type Tab = "artists" | "albums" | "tracks";

export function ArtistCard({ a, onClick }: { a: Artist; onClick: () => void }) {
  return (
    <button className="acard" onClick={onClick}>
      <Avatar name={a.name} size={112} artistId={a.id} />
      <div className="acard-name">{a.name}</div>
      <div className="muted small">{num(a.tracks)} {plural(a.tracks, "трек", "трека", "треков")}</div>
    </button>
  );
}

export function AlbumCard({ al, coverTrack, onClick, showArtist = true }: { al: Album; coverTrack?: string; onClick: () => void; showArtist?: boolean }) {
  return (
    <button className="alcard" onClick={onClick}>
      <Cover trackId={coverTrack ?? al.cover_track} size={150} radius={10} title={al.title} />
      <div className="alcard-title">{al.title}</div>
      <div className="muted small">{al.year ?? ""}{al.type === "single" ? " · сингл" : ""}{al.artists && showArtist ? ` · ${al.artists}` : ""}</div>
    </button>
  );
}

export function LibraryView() {
  const { go, settings, route } = useApp();
  const [q, setQ] = useState(route.q ?? "");
  const [tab, setTab] = useState<Tab>("artists");
  const [res, setRes] = useState<Res | null>(null);
  const [discover, setDiscover] = useState<Artist[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => { api.get<{ artists: Artist[] }>("/api/discover?n=30").then((r) => setDiscover(r.artists)).catch(() => {}); }, []);
  useEffect(() => {
    if (!q.trim()) { setRes(null); return; }
    const ctl = new AbortController();
    setLoading(true);
    const t = window.setTimeout(() => {
      api.get<Res>(`/api/search?q=${encodeURIComponent(q)}&limit=60`, ctl.signal)
        .then((r) => { setRes(r); setLoading(false); if (!r[tab].length) { const alt = (["artists", "tracks", "albums"] as Tab[]).find((k) => r[k].length); if (alt) setTab(alt); } })
        .catch(() => {});
    }, 220);
    return () => { clearTimeout(t); ctl.abort(); };
  }, [q]);

  const playList = (list: Track[], i: number) => player.playQueue(list, i, "Результаты поиска", settings?.genres ?? []);
  return (
    <div className="library">
      <div className="page-head">
        <h1>Библиотека</h1>
        <div className="search"><ISearch size={16} /><input autoFocus placeholder="Исполнители, альбомы, треки" value={q} onChange={(e) => setQ(e.target.value)} />{q && <button className="clear" onClick={() => setQ("")}>×</button>}</div>
      </div>
      {!q.trim() ? (
        <section>
          <div className="section-head"><h2>Случайные исполнители</h2>
            <button className="btn ghost" onClick={() => api.get<{ artists: Artist[] }>("/api/discover?n=30").then((r) => setDiscover(r.artists))}>Ещё</button></div>
          <div className="grid artists">{discover.map((a) => <ArtistCard key={a.id} a={a} onClick={() => go({ view: "artist", id: a.id })} />)}</div>
        </section>
      ) : (
        <>
          <Segmented value={tab} onChange={setTab} options={[
            ["artists", `Исполнители${res ? ` ${res.artists.length}` : ""}`], ["albums", `Альбомы${res ? ` ${res.albums.length}` : ""}`],
            ["tracks", `Треки${res ? ` ${res.tracks.length}` : ""}`]]} />
          {loading && !res ? <Spinner /> : !res ? null :
            tab === "artists" ? (res.artists.length ? <div className="grid artists">{res.artists.map((a) => <ArtistCard key={a.id} a={a} onClick={() => go({ view: "artist", id: a.id })} />)}</div> : <Empty title="Исполнители не найдены" />) :
            tab === "albums" ? (res.albums.length ? <div className="grid albums">{res.albums.map((al) => <AlbumCard key={al.id} al={al} onClick={() => go({ view: "album", id: al.id })} />)}</div> : <Empty title="Альбомы не найдены" />) :
            (res.tracks.length ? <div className="tlist card">{res.tracks.map((t, i) => <TrackRow key={t.id} t={t} onPlay={() => playList(res.tracks, i)} onArtist={(id) => go({ view: "artist", id })} />)}</div> : <Empty title="Треки не найдены" />)}
        </>
      )}
    </div>
  );
}
