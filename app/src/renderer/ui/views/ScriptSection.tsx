import { useEffect, useState } from "react";
import { api, num, Pool, ScriptInfo, Settings } from "../../api";

type ScriptsResp = { scripts: ScriptInfo[]; total: number; counts: Record<string, number> };

/** Настройки → «Письменность названий треков»: исключить из случайного выбора треки с буквами выбранных письменностей. */
export function ScriptSection({ settings, save }: { settings: Settings; save: (p: Partial<Settings>) => void }) {
  const [info, setInfo] = useState<ScriptsResp | null>(null);
  const [pool, setPool] = useState<Pool | null>(null);
  const excluded = settings.title_scripts_excluded ?? [];
  useEffect(() => { api.get<ScriptsResp>("/api/scripts").then(setInfo).catch(() => {}); }, []);
  useEffect(() => { api.post<Pool>("/api/pool", { genres: [] }).then(setPool).catch(() => {}); }, [excluded.join(",")]);
  const toggle = (id: string) => {
    const s = new Set(excluded);
    if (s.has(id)) s.delete(id); else s.add(id);
    save({ title_scripts_excluded: [...s] });
  };
  return (
    <section className="card">
      <h2>Письменность названий треков</h2>
      <div className="setting-hint script-hint">Отмеченные письменности исключаются из случайного воспроизведения: трек пропускается, если в его названии есть
        хотя бы одна буква такой письменности («Love / Любовь» при исключении кириллицы пропускается, «Love Story» — нет). Цифры, знаки и эмодзи не учитываются.
        Действует вместе с жанрами и фильтром стран; трек, запущенный вручную из «Библиотеки», играет всегда.</div>
      <div className="script-list">
        {(info?.scripts ?? []).map((s) => (
          <label key={s.id} className={`script-item ${excluded.includes(s.id) ? "on" : ""}`}>
            <input type="checkbox" checked={excluded.includes(s.id)} onChange={() => toggle(s.id)} />
            <span className="sn">{s.title}</span>
            {s.sample && <span className="ss">{s.sample}</span>}
            <span className="sc">{info?.counts[s.id] ? num(info.counts[s.id]) : "—"}</span>
          </label>
        ))}
      </div>
      {pool && <p className="fine">{excluded.length
        ? <>Исключено из случайного выбора: <b>{num(pool.script_excluded_tracks)}</b> из {num(pool.tracks)} треков{pool.script_excluded_artists ? `; у ${num(pool.script_excluded_artists)} исполнителей подходящих треков не осталось, они не выбираются` : ""}.</>
        : <>Все письменности разрешены. Число справа — сколько треков каталога содержат буквы этой письменности.</>}
        {" "}Иероглифы (ханьцзы, кандзи, ханча) — одна письменность; японская кана — отдельно.</p>}
    </section>
  );
}
