import { useEffect, useState } from "react";
import { api, bridge, CacheInfo, num, Storage } from "../../api";
import { useApp } from "../App";
import { Segmented } from "../common";
import { CountrySection } from "./CountrySection";

function Row({ title, hint, children }: { title: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return <div className="setting"><div><div className="setting-title">{title}</div>{hint && <div className="setting-hint">{hint}</div>}</div><div className="setting-ctl">{children}</div></div>;
}

const mb = (b: number) => (b >= 1e6 ? `${(b / 1048576).toFixed(b >= 1e8 ? 0 : 1)} МБ` : `${Math.max(1, Math.round(b / 1024))} КБ`);
const date = (t: number) => new Date(t * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });

function StorageSection({ months, onMonths }: { months: number; onMonths: (m: number) => void }) {
  const [s, setS] = useState<Storage | null>(null);
  const [cache, setCache] = useState<CacheInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState("");
  const load = () => {
    api.get<Storage>("/api/storage").then(setS).catch(() => {});
    bridge?.cache.info().then(setCache).catch(() => {});
  };
  useEffect(load, [months]);
  const clear = async () => {
    setBusy(true); setDone("");
    try {
      const [st, c] = await Promise.all([api.post<Storage>("/api/storage/clear-cache"), bridge ? bridge.cache.clear() : Promise.resolve(null)]);
      setS(st); if (c) setCache(c);
      setDone("Кеш очищен");
    } finally { setBusy(false); }
  };
  const cacheBytes = (cache?.httpBytes ?? 0);
  return (
    <section className="card">
      <h2>История и место на диске</h2>
      <Row title="Хранить подробную историю" hint="Старые записи раздела «История» удаляются автоматически. Счётчики прослушиваний и защита от повторов сохраняются всегда.">
        <select value={months} onChange={(e) => onMonths(+e.target.value)}>
          <option value={3}>3 месяца</option><option value={6}>6 месяцев</option><option value={12}>12 месяцев</option><option value={0}>Бессрочно</option>
        </select>
      </Row>
      {s && <div className="country-box">
        <p>Подробная история: <b>{num(s.history_rows)}</b> {s.history_rows ? `записей (с ${date(s.history_oldest!)})` : "записей"}.
          Статистика за всё время: {num(s.stats.plays)} прослушиваний, {num(s.stats.tracks)} треков, {num(s.stats.artists)} исполнителей, {s.stats.hours} ч.</p>
        <p className="muted">База истории и настроек: {mb(s.state_db_bytes)}{s.last_maintenance ? ` · последнее обслуживание ${new Date(s.last_maintenance.at * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}` : ""}</p>
      </div>}
      <Row title="Кеш" hint={<>Обложки и фото исполнителей хранятся для быстрого показа{cache ? ` (не больше ${mb(cache.httpLimit)}, старые вытесняются сами)` : ""}. Очистка не затрагивает историю, каталоги, вход и настройки, музыка продолжит играть.</>}>
        <span className="val wide">{cache ? mb(cacheBytes) : s ? `${num(s.covers_cached)} обл.` : ""}{cache && cache.logBytes ? <span className="muted"> · журналы {mb(cache.logBytes)}</span> : null}</span>
        <button className="btn" disabled={busy} onClick={clear}>{busy ? "Очищаю…" : done || "Очистить кеш"}</button>
      </Row>
    </section>
  );
}

export function SettingsView() {
  const { settings, saveSettings, status, refresh } = useApp();
  if (!settings) return null;
  return (
    <div className="settings">
      <div className="page-head"><h1>Настройки</h1></div>

      <section className="card">
        <h2>Воспроизведение</h2>
        <Row title="Не повторять исполнителей" hint={`Исполнитель не прозвучит снова, пока не сыграют ${settings.artist_cooldown} других (если в жанре их достаточно).`}>
          <input type="range" min={0} max={400} step={10} value={settings.artist_cooldown} onChange={(e) => saveSettings({ artist_cooldown: +e.target.value })} />
          <span className="val">{settings.artist_cooldown}</span>
        </Row>
        <Row title="Не повторять треки" hint="Трек не попадёт в подборку снова в течение этого срока.">
          <select value={settings.track_cooldown_days} onChange={(e) => saveSettings({ track_cooldown_days: +e.target.value })}>
            {[7, 14, 30, 90, 365].map((d) => <option key={d} value={d}>{d} дней</option>)}
          </select>
        </Row>
      </section>

      <StorageSection months={settings.history_months ?? 12} onMonths={(m) => saveSettings({ history_months: m })} />

      <CountrySection settings={settings} save={saveSettings} online={status?.auth === "ok"} />

      <section className="card">
        <h2>Оформление</h2>
        <Row title="Тема"><Segmented value={settings.theme} onChange={(v) => saveSettings({ theme: v })} options={[["auto", "Как в системе"], ["light", "Светлая"], ["dark", "Тёмная"]]} /></Row>
      </section>

      <section className="card">
        <h2>Аккаунт Яндекса</h2>
        <Row title={status?.account?.name ? `${status.account.name}${status.account.has_plus ? " · Плюс" : " · без Плюса"}` : status?.auth === "offline" ? "Нет связи с Яндексом" : "Вход не выполнен"}
          hint={status?.auth === "offline" ? status.conn_error : "Токен хранится зашифрованным средствами Windows (DPAPI) и доступен только вашей учётной записи."}>
          {status?.auth === "ok" ? <button className="btn" onClick={() => api.post("/api/auth/logout").then(refresh)}>Выйти</button>
            : <button className="btn" onClick={() => api.post("/api/reconnect").then(refresh)}>Подключиться</button>}
        </Row>
      </section>

      <section className="card">
        <h2>О приложении</h2>
        <Row title="DeepWave 1.3" hint={<>Неофициальный клиент Яндекс Музыки · папка проекта: {status?.root}<br />Python {status?.python}</>}>
          {bridge && <button className="btn ghost" onClick={() => bridge!.showLog()}>Журнал сервиса</button>}
        </Row>
      </section>
    </div>
  );
}
