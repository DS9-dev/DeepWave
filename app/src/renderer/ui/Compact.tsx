// Компактный плеер: то же окно, тот же аудиодвижок и та же сессия — меняется только вид.
import { useEffect, useState } from "react";
import { bridge, fmtTime } from "../api";
import { player } from "../player";
import { useApp } from "./App";
import { Cover, useCover, usePlayer } from "./common";
import { IClose, IExpand, IMute, INext, IPause, IPin, IPlay, IPrev, IVolume } from "./icons";
import { Slider } from "./PlayerBar";

export function CompactPlayer() {
  const st = usePlayer();
  const { settings, setMode, status } = useApp();
  const [pinned, setPinned] = useState(false);
  const [lastVol, setLastVol] = useState(0.8);
  useEffect(() => { bridge?.win.getMode().then((m) => setPinned(m.pinned)); }, []);
  const t = st.current;
  const bg = useCover(t?.id, t?.cover);
  const dur = st.duration || (t?.duration_ms ? t.duration_ms / 1000 : 0);
  const needAuth = status?.auth === "none";
  const togglePin = async () => setPinned(bridge ? await bridge.win.setPinned(!pinned) : !pinned);
  return (
    <div className="compact">
      {bg && <div className="cp-bg" style={{ backgroundImage: `url("${bg}")` }} />}
      <div className="cp-cover">
        {t ? <Cover trackId={t.id} src={t.cover} size={124} radius={12} title={t.album || t.title} />
          : <div className="cp-empty-cover"><span className="brand-mark" /></div>}
      </div>
      <div className="cp-main">
        <div className="cp-top">
          <div className="cp-meta">
            <div className="cp-title" title={t?.title}>{t ? t.title : "DeepWave"}</div>
            <div className="cp-artist" title={t?.artists}>
              {needAuth ? "Войдите в полном режиме" : t ? t.artists : st.starting ? "Подбираю трек…" : st.error || "Ничего не играет"}
            </div>
          </div>
          <div className="cp-actions">
            <button className={`cp-btn ${pinned ? "on" : ""}`} title={pinned ? "Не поверх окон" : "Поверх всех окон"} onClick={togglePin}><IPin size={15} /></button>
            <button className="cp-btn" title="Полный интерфейс (Ctrl+M)" onClick={() => setMode("full")}><IExpand size={15} /></button>
            <button className="cp-btn close" title="Закрыть DeepWave" onClick={() => bridge?.win.close()}><IClose size={14} /></button>
          </div>
        </div>
        <div className="cp-progress">
          <span className="time">{fmtTime(st.position)}</span>
          <Slider value={st.position} max={dur || 1} onCommit={(v) => player.seek(v)} />
          <span className="time">{fmtTime(dur)}</span>
        </div>
        <div className="cp-controls">
          <div className="cp-transport">
            <button className="icon-btn" onClick={() => player.prev()} disabled={!t} title="Предыдущий"><IPrev size={17} /></button>
            <button className={`play-btn small ${st.buffering && st.playing ? "loading" : ""}`} disabled={needAuth}
              onClick={() => (t ? player.toggle() : player.startRadio(settings?.genres ?? []))} title="Пауза / продолжить">
              {st.playing ? <IPause size={17} /> : <IPlay size={17} />}
            </button>
            <button className="icon-btn" onClick={() => player.next()} disabled={!t && !st.starting} title="Следующий"><INext size={17} /></button>
          </div>
          <div className="cp-volume">
            <button className="icon-btn small" title="Без звука" onClick={() => { if (st.volume > 0) { setLastVol(st.volume); player.setVolume(0); } else player.setVolume(lastVol || 0.8); }}>
              {st.volume === 0 ? <IMute size={15} /> : <IVolume size={15} />}
            </button>
            <Slider className="vol mini" value={st.volume} max={1} onChange={(v) => player.setVolume(v)} onCommit={(v) => player.setVolume(v)} />
          </div>
        </div>
      </div>
    </div>
  );
}
