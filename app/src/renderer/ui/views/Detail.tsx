import { useEffect, useState } from "react";
import { Album, api, Artist, fmtTime, num, plural, Track } from "../../api";
import { player } from "../../player";
import { useApp } from "../App";
import { Avatar, Cover, Empty, Spinner, TrackRow } from "../common";
import { IBack, IPlay, IShuffle } from "../icons";
import { AlbumCard } from "./Library";

function BackBtn() {
  const { back, canBack } = useApp();
  return canBack ? <button className="back" onClick={back}><IBack size={16} />Назад</button> : null;
}

const shuffled = <T,>(a: T[]) => { const b = [...a]; for (let i = b.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [b[i], b[j]] = [b[j], b[i]]; } return b; };

export function ArtistView({ id }: { id: number }) {
  const { go, settings } = useApp();
  const [d, setD] = useState<{ artist: Artist; albums: Album[]; tracks: Track[] } | null | "none">(null);
  useEffect(() => { api.get<{ artist: Artist; albums: Album[]; tracks: Track[] }>(`/api/artist/${id}`).then(setD).catch(() => setD("none")); }, [id]);
  if (d === null) return <Spinner />;
  if (d === "none") return <><BackBtn /><Empty title="Исполнителя нет в каталоге">Его треки ещё не собраны.</Empty></>;
  const { artist, albums, tracks } = d;
  const genres = settings?.genres ?? [];
  return (
    <div className="detail">
      <BackBtn />
      <header className="dhead">
        <Avatar name={artist.name} size={168} artistId={artist.id} />
        <div>
          <div className="eyebrow">Исполнитель</div>
          <h1>{artist.name}</h1>
          <p className="muted">{num(tracks.length)} {plural(tracks.length, "трек", "трека", "треков")} · {albums.length} {plural(albums.length, "релиз", "релиза", "релизов")} в каталоге</p>
          <div className="row gap">
            <button className="btn primary round" onClick={() => player.playQueue(shuffled(tracks), 0, artist.name, genres)}><IShuffle size={16} />Перемешать</button>
            <button className="btn round" onClick={() => player.playQueue(tracks, 0, artist.name, genres)}><IPlay size={15} />По порядку</button>
          </div>
        </div>
      </header>
      {albums.length > 0 && <section>
        <h2 className="section-title">Альбомы и синглы</h2>
        <div className="grid albums">{albums.map((al) => <AlbumCard key={al.id} al={al} showArtist={false} onClick={() => go({ view: "album", id: al.id })} />)}</div>
      </section>}
      <section>
        <h2 className="section-title">Все треки</h2>
        <div className="tlist card">{tracks.map((t, i) => <TrackRow key={t.id} t={t} index={i + 1} onPlay={() => player.playQueue(tracks, i, artist.name, genres)} />)}</div>
      </section>
    </div>
  );
}

export function AlbumView({ id }: { id: number }) {
  const { go, settings } = useApp();
  const [d, setD] = useState<{ album: Album; tracks: Track[] } | null | "none">(null);
  useEffect(() => { api.get<{ album: Album; tracks: Track[] }>(`/api/album/${id}`).then(setD).catch(() => setD("none")); }, [id]);
  if (d === null) return <Spinner />;
  if (d === "none") return <><BackBtn /><Empty title="Альбом не найден" /></>;
  const { album, tracks } = d;
  const total = tracks.reduce((a, t) => a + (t.duration_ms || 0), 0) / 1000;
  const genres = settings?.genres ?? [];
  return (
    <div className="detail">
      <BackBtn />
      <header className="dhead">
        <Cover trackId={album.cover_track} size={200} radius={14} title={album.title} />
        <div>
          <div className="eyebrow">{album.type === "single" ? "Сингл" : album.type === "compilation" ? "Сборник" : "Альбом"}</div>
          <h1>{album.title}</h1>
          <p className="muted">
            {album.artist_id ? <a onClick={() => go({ view: "artist", id: album.artist_id! })}>{album.artists}</a> : album.artists}
            {album.year ? ` · ${album.year}` : ""} · {tracks.length} {plural(tracks.length, "трек", "трека", "треков")}, {fmtTime(total)}
          </p>
          <button className="btn primary round" onClick={() => player.playQueue(tracks, 0, album.title, genres)}><IPlay size={15} />Слушать</button>
        </div>
      </header>
      <div className="tlist card">{tracks.map((t, i) => <TrackRow key={t.id} t={t} index={i + 1} showAlbum={false} onPlay={() => player.playQueue(tracks, i, album.title, genres)} />)}</div>
    </div>
  );
}
