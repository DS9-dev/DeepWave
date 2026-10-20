import { useRef, useState } from "react";
import { fmtTime } from "../api";
import { player } from "../player";
import { useApp } from "./App";
import { Cover, usePlayer } from "./common";
import { ICompact, IMute, INext, IPause, IPlay, IPrev, IVolume } from "./icons";

export function Slider({ value, max, onChange, onCommit, className }: {
  value: number; max: number; onChange?: (v: number) => void; onCommit: (v: number) => void; className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<number | null>(null);
  const at = (x: number) => {
    const r = ref.current!.getBoundingClientRect();
    return Math.min(1, Math.max(0, (x - r.left) / r.width)) * max;
  };
  const shown = drag ?? value;
  const pct = max > 0 ? (shown / max) * 100 : 0;
  return (
    <div ref={ref} className={`slider ${className ?? ""} ${drag !== null ? "drag" : ""}`}
      onPointerDown={(e) => { (e.target as HTMLElement).setPointerCapture(e.pointerId); const v = at(e.clientX); setDrag(v); onChange?.(v); }}
      onPointerMove={(e) => { if (drag !== null) { const v = at(e.clientX); setDrag(v); onChange?.(v); } }}
      onPointerUp={(e) => { if (drag !== null) { onCommit(at(e.clientX)); setDrag(null); } }}>
      <div className="slider-track"><div className="slider-fill" style={{ width: `${pct}%` }} /></div>
      <div className="slider-knob" style={{ left: `${pct}%` }} />
    </div>
  );
}

export function PlayerBar() {
  const st = usePlayer();
  const { go, settings, setMode } = useApp();
  const t = st.current;
  const [lastVol, setLastVol] = useState(0.8);
  const dur = st.duration || (t?.duration_ms ? t.duration_ms / 1000 : 0);
  return (
    <footer className="playerbar">
      <div className="pb-left">
        {t ? <>
          <button className="pb-cover" onClick={() => go({ view: "listen" })}><Cover trackId={t.id} src={t.cover} size={50} radius={8} title={t.album || t.title} /></button>
          <div className="pb-meta">
            <div className="pb-title" title={t.title}>{t.title}</div>
            <div className="pb-artist">
              {t.artist_id ? <a onClick={() => go({ view: "artist", id: t.artist_id! })}>{t.artists}</a> : t.artists}
            </div>
          </div>
        </> : <div className="pb-meta muted">{st.starting ? "Подбираю трек…" : "Ничего не играет"}</div>}
      </div>
      <div className="pb-center">
        <div className="pb-controls">
          <button className="icon-btn" onClick={() => player.prev()} disabled={!t} title="Предыдущий (Ctrl+←)"><IPrev size={18} /></button>
          <button className={`play-btn ${st.buffering && st.playing ? "loading" : ""}`} disabled={!t && !st.starting}
            onClick={() => (t ? player.toggle() : player.startRadio(settings?.genres ?? []))} title="Пауза / продолжить (пробел)">
            {st.playing ? <IPause size={20} /> : <IPlay size={20} />}
          </button>
          <button className="icon-btn" onClick={() => player.next()} disabled={!t && !st.starting} title="Следующий (Ctrl+→)"><INext size={18} /></button>
        </div>
        <div className="pb-progress">
          <span className="time">{fmtTime(st.position)}</span>
          <Slider value={st.position} max={dur || 1} onCommit={(v) => player.seek(v)} />
          <span className="time">{fmtTime(dur)}</span>
        </div>
      </div>
      <div className="pb-right">
        {st.error && <span className="pb-error" title={st.error}>{st.error}</span>}
        <button className="icon-btn" title="Без звука" onClick={() => { if (st.volume > 0) { setLastVol(st.volume); player.setVolume(0); } else player.setVolume(lastVol || 0.8); }}>
          {st.volume === 0 ? <IMute size={18} /> : <IVolume size={18} />}
        </button>
        <Slider className="vol" value={st.volume} max={1} onChange={(v) => player.setVolume(v)} onCommit={(v) => player.setVolume(v)} />
        <button className="icon-btn" title="Компактный плеер (Ctrl+M)" onClick={() => setMode("compact")}><ICompact size={18} /></button>
      </div>
    </footer>
  );
}
