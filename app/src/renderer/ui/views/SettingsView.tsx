import { useEffect, useState } from "react";
import { api, bridge, num } from "../../api";
import { useApp } from "../App";
import { Segmented, Toggle } from "../common";

type Countries = { running: boolean; done?: number; todo?: number; artists: number; checked: number; with_country: number;
  coverage_pct: number | null; ru_artists: number; top_values: [string, number][] };

function Row({ title, hint, children }: { title: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return <div className="setting"><div><div className="setting-title">{title}</div>{hint && <div className="setting-hint">{hint}</div>}</div><div className="setting-ctl">{children}</div></div>;
}

export function SettingsView() {
  const { settings, saveSettings, status, refresh } = useApp();
  const [c, setC] = useState<Countries | null>(null);
  useEffect(() => {
    let alive = true;
    const poll = () => api.get<Countries>("/api/countries").then((r) => alive && setC(r)).catch(() => {});
    poll();
    const t = window.setInterval(poll, 2000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  if (!settings) return null;
  const covered = c && c.checked > 0;
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

      <section className="card">
        <h2>Исполнители из России</h2>
        <Row title="Исключать российских исполнителей" hint={<>
          Страна берётся из данных Яндекса об исполнителе (поле «страны»), а не из языка песни или жанра «Русский рок».
          Исполнители, у которых страна неизвестна, <b>не исключаются</b>.</>}>
          <Toggle checked={settings.exclude_ru} disabled={!covered} onChange={(v) => saveSettings({ exclude_ru: v })} />
        </Row>
        {c && <div className="country-box">
          {covered ? <p>Проверено {num(c.checked)} из {num(c.artists)} исполнителей. Страна известна для <b>{c.coverage_pct}%</b> ({num(c.with_country)}); из них Россия — {num(c.ru_artists)}.
            {c.top_values.length > 0 && <span className="muted"> Значения: {c.top_values.slice(0, 8).map(([v, n]) => `${v} (${n})`).join(", ")}.</span>}</p>
            : <p className="muted">Страны исполнителей ещё не определены — фильтр недоступен.</p>}
          {c.running ? <p className="muted">Определяю страны… {num(c.done || 0)} / {num(c.todo || 0)}</p> :
            <button className="btn" disabled={status?.auth !== "ok"} onClick={() => api.post("/api/countries/start").then(() => api.get<Countries>("/api/countries").then(setC))}>
              {covered && c.checked < c.artists ? "Проверить новых исполнителей" : covered ? "Обновить" : "Определить страны исполнителей"}</button>}
          <p className="fine">Около 1 запроса на 100 исполнителей. После пополнения каталога проверьте новых исполнителей.</p>
        </div>}
      </section>

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
        <Row title="DeepWave 1.1" hint={<>Неофициальный клиент Яндекс Музыки · папка проекта: {status?.root}<br />Python {status?.python}</>}>
          {bridge && <button className="btn ghost" onClick={() => bridge!.showLog()}>Журнал сервиса</button>}
        </Row>
      </section>
    </div>
  );
}
