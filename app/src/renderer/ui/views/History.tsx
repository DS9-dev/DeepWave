import { useEffect, useState } from "react";
import { api, fmtTime, num, Track } from "../../api";
import { player } from "../../player";
import { useApp } from "../App";
import { Cover, Empty, Spinner } from "../common";

type Item = { play_id: number; id: string; artist_id: number | null; title: string; artists: string; started_at: number;
  listened_s: number; duration_s: number; status: string; origin: string };

const STATUS: Record<string, string> = { finished: "дослушан", skipped: "пропущен", error: "ошибка", playing: "играет", stopped: "прерван" };

export function HistoryView() {
  const { go, settings } = useApp();
  const [d, setD] = useState<{ items: Item[]; stats: { plays: number; artists: number; hours: number } } | null>(null);
  useEffect(() => { api.get<typeof d>("/api/history?limit=300").then(setD).catch(() => {}); }, []);
  if (!d) return <Spinner />;
  const days = new Map<string, Item[]>();
  d.items.forEach((it) => {
    const k = new Date(it.started_at * 1000).toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
    days.set(k, [...(days.get(k) || []), it]);
  });
  return (
    <div className="history">
      <div className="page-head">
        <h1>История</h1>
        <div className="stats">
          <div><b>{num(d.stats.plays)}</b>прослушиваний</div><div><b>{num(d.stats.artists)}</b>исполнителей</div><div><b>{d.stats.hours}</b>часов</div>
        </div>
      </div>
      {!d.items.length ? <Empty title="История пуста">Здесь появятся прослушанные треки — по ним приложение избегает повторов.</Empty> :
        [...days.entries()].map(([day, items]) => (
          <section key={day} className="card">
            <div className="group-label">{day}</div>
            <div className="tlist">
              {items.map((it) => (
                <div key={it.play_id} className="trow" onDoubleClick={() => player.playQueue([{ id: it.id } as Track], 0, "История", settings?.genres ?? [])}>
                  <span className="trow-time">{new Date(it.started_at * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</span>
                  <Cover trackId={it.id} size={36} radius={6} title={it.title} />
                  <div className="trow-main">
                    <div className="trow-title">{it.title}</div>
                    <div className="trow-sub">{it.artist_id ? <a onClick={() => go({ view: "artist", id: it.artist_id! })}>{it.artists}</a> : it.artists}</div>
                  </div>
                  <span className={`pill ${it.status}`}>{STATUS[it.status] ?? it.status}</span>
                  <div className="trow-dur">{fmtTime(it.listened_s)}{it.duration_s ? ` / ${fmtTime(it.duration_s)}` : ""}</div>
                </div>
              ))}
            </div>
          </section>
        ))}
    </div>
  );
}
