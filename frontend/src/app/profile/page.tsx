"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

type User = { id: string; role: string; display_name: string | null };
type View = { id: string; event_id: string; title: string; city: string; channel: string; viewed_at: string };
const bot = process.env.NEXT_PUBLIC_TELEGRAM_BOT_USERNAME;

export default function ProfilePage() {
  const [user, setUser] = useState<User | null>(null);
  const [items, setItems] = useState<View[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const widget = useRef<HTMLDivElement>(null);
  async function refresh() {
    const response = await fetch("/backend/api/v1/users/me", { credentials: "include", cache: "no-store" });
    if (response.ok) setUser(await response.json());
    else setUser(null);
  }
  useEffect(() => { const timer = window.setTimeout(() => { void refresh(); }, 0); return () => window.clearTimeout(timer); }, []);
  useEffect(() => {
    if (!user) return;
    void fetch(`/backend/api/v1/users/me/history?page=${page}`, { credentials: "include", cache: "no-store" })
      .then(r => r.json()).then(data => { setItems(data.items); setTotal(data.total); });
  }, [user, page]);
  useEffect(() => {
    if (user || !bot || !widget.current) return;
    const global = window as Window & { onTelegramAuth?: (data: unknown) => void };
    global.onTelegramAuth = async data => {
      const response = await fetch("/backend/api/v1/auth/telegram", {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
      });
      if (response.ok) { setError(""); await refresh(); }
      else setError("Не удалось подтвердить вход через Telegram");
    };
    const script = document.createElement("script");
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.async = true;
    script.setAttribute("data-telegram-login", bot);
    script.setAttribute("data-size", "large");
    script.setAttribute("data-onauth", "onTelegramAuth(user)");
    widget.current.appendChild(script);
    return () => { script.remove(); delete global.onTelegramAuth; };
  }, [user]);
  async function logout() {
    const csrf = document.cookie.split("; ").find(x => x.startsWith("user_csrf="))?.split("=")[1] || "";
    await fetch("/backend/api/v1/auth/logout", { method: "POST", credentials: "include",
      headers: { "X-CSRF-Token": decodeURIComponent(csrf) } });
    setUser(null); setItems([]);
  }
  return <section className="container page-content"><h1>Мой профиль</h1>
    {!user ? <><p>Войдите через Telegram, чтобы видеть историю из бота и сайта.</p>
      {bot ? <div ref={widget} /> : <p>Вход станет доступен после настройки имени бота.</p>}
      {error && <p role="alert">{error}</p>}</> : <>
      <p>{user.display_name || "Пользователь"} · {user.role}</p>
      <button className="button" onClick={() => void logout()}>Выйти</button>
      <h2>Моя история</h2>
      {items.length === 0 && <p>Вы ещё не открывали карточки мероприятий.</p>}
      <ul>{items.map(view => <li key={view.id}><Link href={`/events/${encodeURIComponent(view.event_id)}`}>{view.title}</Link> · {view.city} · {view.channel === "telegram" ? "Telegram" : "Сайт"}</li>)}</ul>
      {page > 1 && <button onClick={() => setPage(page - 1)}>Назад</button>}
      {page * 20 < total && <button onClick={() => setPage(page + 1)}>Далее</button>}
    </>}
  </section>;
}
