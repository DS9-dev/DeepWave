// Общие элементы: обложка, аватар, строка трека, хук обложек, индикатор загрузки.
import { useEffect, useState, useSyncExternalStore } from "react";
import { artistImage, coverFor, fmtTime, onCovers, Track } from "../api";
import { player } from "../player";
import { IPlay, IWave } from "./icons";

export const usePlayer = () => useSyncExternalStore(player.subscribe, player.getSnapshot);

export function useCover(trackId?: string | null, known?: string | null) {
  const [, force] = useState(0);
  useEffect(() => onCovers(() => force((n) => n + 1)), []);
  if (known) return known;
  return coverFor(trackId) ?? null;
}

const hue = (s: string) => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };

export function Cover({ trackId, src, size = 48, radius = 8, title = "" }: { trackId?: string | null; src?: string | null; size?: number; radius?: number; title?: string }) {
  const url = useCover(trackId, src);
  const [failed, setFailed] = useState(false);
  const h = hue(title || trackId || "x");
  return (
    <div className="cover" style={{ width: size, height: size, borderRadius: radius,
      background: `linear-gradient(135deg, hsl(${h} 45% 52%), hsl(${(h + 40) % 360} 50% 32%))` }}>
      {url && !failed ? <img src={url} alt="" loading="lazy" onError={() => setFailed(true)} draggable={false} />
        : <IWave size={Math.max(14, size * 0.38)} />}
    </div>
  );
}

export function Avatar({ name, size = 96, artistId }: { name: string; size?: number; artistId?: number | null }) {
  const [, force] = useState(0);
  useEffect(() => onCovers(() => force((n) => n + 1)), []);
  const [failed, setFailed] = useState(false);
  const photo = artistImage(artistId);
  const h = hue(name);
  const bg = `radial-gradient(circle at 30% 25%, hsl(${h} 60% 62%), hsl(${(h + 50) % 360} 55% 30%))`;
  if (photo && !failed) return <div className="avatar photo" style={{ width: size, height: size, background: bg }}><img src={photo} alt="" loading="lazy" draggable={false} onError={() => setFailed(true)} /></div>;
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  return (
    <div className="avatar" style={{ width: size, height: size, fontSize: size * 0.36,
      background: `radial-gradient(circle at 30% 25%, hsl(${h} 60% 62%), hsl(${(h + 50) % 360} 55% 30%))` }}>{initials || "♪"}</div>
  );
}

export function TrackRow({ t, index, onPlay, showAlbum = true, onArtist }: {
  t: Track; index?: number; onPlay: () => void; showAlbum?: boolean; onArtist?: (id: number) => void;
}) {
  const st = usePlayer();
  const active = st.current?.id === t.id;
  return (
    <div className={`trow ${active ? "active" : ""}`} onDoubleClick={onPlay}>
      <button className="trow-play" onClick={onPlay} aria-label="Слушать">
        {active && st.playing ? <span className="eq"><i /><i /><i /></span> : <><span className="trow-idx">{index ?? ""}</span><IPlay size={14} /></>}
      </button>
      <Cover trackId={t.id} size={36} radius={6} title={t.album || t.title} />
      <div className="trow-main">
        <div className="trow-title">{t.title}{t.version ? <span className="muted"> · {t.version}</span> : null}</div>
        <div className="trow-sub">
          {onArtist && t.artist_id ? <a onClick={(e) => { e.stopPropagation(); onArtist(t.artist_id!); }}>{t.artists}</a> : t.artists}
        </div>
      </div>
      {showAlbum && <div className="trow-album">{t.album}{t.year ? ` · ${t.year}` : ""}</div>}
      <div className="trow-dur">{t.duration_ms ? fmtTime(t.duration_ms / 1000) : ""}</div>
    </div>
  );
}

export const Spinner = ({ label }: { label?: string }) => <div className="spinner-wrap"><div className="spinner" />{label && <span>{label}</span>}</div>;

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return <div className="empty"><div className="empty-title">{title}</div>{children && <div className="empty-text">{children}</div>}</div>;
}

export function Toggle({ checked, onChange, disabled }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return <button className={`toggle ${checked ? "on" : ""}`} role="switch" aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)}><i /></button>;
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="segmented">
      {options.map(([v, l]) => <button key={v} className={v === value ? "on" : ""} onClick={() => onChange(v)}>{l}</button>)}
    </div>
  );
}
