import { useEffect, useState } from "react";
import { api, bridge } from "../../api";
import { useApp } from "../App";

type AuthState = { status: string; verification_url?: string; user_code?: string; error?: string; auth?: string };

export function SignIn() {
  const { refresh } = useApp();
  const [s, setS] = useState<AuthState>({ status: "idle" });
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (s.status !== "pending" && s.status !== "starting") return;
    const t = window.setInterval(async () => {
      const r = await api.get<AuthState>("/api/auth/state").catch(() => null);
      if (!r) return;
      setS(r);
      if (r.status === "ok" || r.auth === "ok") { clearInterval(t); refresh(); }
    }, 2000);
    return () => clearInterval(t);
  }, [s.status]);
  const start = async () => setS(await api.post<AuthState>("/api/auth/start"));
  const open = () => s.verification_url && (bridge ? bridge.openExternal(s.verification_url) : window.open(s.verification_url));
  return (
    <div className="signin">
      <span className="brand-mark big" />
      <h1>Добро пожаловать в DeepWave</h1>
      <p className="muted">Для воспроизведения нужен вход в ваш аккаунт Яндекса с подпиской Плюс.<br />Логин и пароль вводятся только на сайте Яндекса — приложение их не видит.</p>
      {s.status === "pending" ? (
        <div className="code-box">
          <ol>
            <li>Откройте страницу Яндекса: <a onClick={open}>{s.verification_url}</a></li>
            <li>Введите код:</li>
          </ol>
          <button className="code" onClick={() => { navigator.clipboard?.writeText(s.user_code || ""); setCopied(true); }} title="Скопировать">{s.user_code}</button>
          <div className="muted small">{copied ? "Код скопирован" : "Нажмите на код, чтобы скопировать"}</div>
          <div className="row gap center"><button className="btn primary" onClick={open}>Открыть страницу входа</button></div>
          <p className="muted small">Жду подтверждения…</p>
        </div>
      ) : (
        <>
          {s.status === "error" && <div className="note error">Не удалось войти: {s.error}</div>}
          <button className="btn primary big" onClick={start} disabled={s.status === "starting"}>{s.status === "starting" ? "Получаю код…" : "Войти через Яндекс"}</button>
        </>
      )}
    </div>
  );
}
