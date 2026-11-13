"use client";
import { useCallback, useEffect, useRef, useState } from "react";

type Row = {
  id: string;
  created_at?: string;
  title?: string;
  city?: string;
  venue?: string;
  status?: string;
  is_disputed?: boolean;
  description?: string;
  message?: string;
  action?: string;
  entity_type?: string;
  entity_id?: string;
  actor?: string;
  changes?: unknown;
};
const apiPath = (path: string) => `/backend/api/v1/admin/${path}`;
function csrf() { return document.cookie.split("; ").map(x => x.trim()).find(x => x.startsWith("admin_csrf="))?.split("=")[1] || ""; }
const tabs = [["events", "События"], ["moderation", "Модерация"], ["audit", "История"], ["feedback", "Заявки"]];

export default function AdminPage() {
  const [authorized, setAuthorized] = useState(false), [user, setUser] = useState(""), [password, setPassword] = useState("");
  const [role, setRole] = useState<"admin" | "moderator" | "">("");
  const [tab, setTab] = useState("events"), [rows, setRows] = useState<Row[]>([]), [query, setQuery] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState(""), [message, setMessage] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  useEffect(() => { void fetch(apiPath("session"), { credentials: "include", cache: "no-store" }).then(async r => { if (r.ok) { const session = await r.json(); setRole(session.role); setAuthorized(true); } }).catch(() => undefined); }, []);
  const handleUnauthorized = useCallback(() => { setAuthorized(false); setRole(""); setRows([]); setError("Сессия завершилась. Войдите в систему."); }, []);
  const load = useCallback(async (section = tab) => { setBusy(true); setError(""); try { const url = section === "events" ? `events?q=${encodeURIComponent(query)}` : section; const r = await fetch(apiPath(url), { credentials: "include", cache: "no-store" }); if (r.status === 401) { handleUnauthorized(); return; } if (!r.ok) throw new Error("Не удалось загрузить данные"); const d = await r.json(); setRows(section === "events" ? d.events : d); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }, [query, tab, handleUnauthorized]);
  useEffect(() => { if (!authorized) return; const timer = window.setTimeout(() => { void load(); }, 0); return () => window.clearTimeout(timer); }, [authorized, tab, load]);
  async function write(path: string, method: string, body?: unknown) { const r = await fetch(apiPath(path), { method, credentials: "include", headers: { "Content-Type": "application/json", "X-CSRF-Token": decodeURIComponent(csrf()) }, body: body ? JSON.stringify(body) : undefined }); if (r.status === 401) { handleUnauthorized(); throw new Error("Сессия завершилась. Войдите в систему."); } if (!r.ok) { const d = await r.json().catch(() => null); throw new Error(d?.error?.message || "Операция не выполнена"); } return r.json(); }
  async function login(e: React.FormEvent) { e.preventDefault(); setBusy(true); setError(""); try { const r = await fetch(apiPath("login"), { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: user, password }) }); if (!r.ok) throw new Error("Проверьте логин и пароль"); const session = await fetch(apiPath("session"), { credentials: "include", cache: "no-store" }); if (!session.ok) throw new Error("Не удалось подтвердить сессию. Проверьте настройки cookie."); const data = await session.json(); setRole(data.role); setAuthorized(true); setPassword(""); setError(""); } catch (x) { setError((x as Error).message); } finally { setBusy(false); } }
  async function logout() { try { await write("logout", "POST"); setAuthorized(false); setRole(""); setRows([]); setPassword(""); setSelectedFile(null); } catch (e) { setError((e as Error).message); } }
  async function status(id: string, value: string) { if ((value === "cancelled" || value === "draft") && !confirm(`Изменить статус на ${value}?`)) return; try { await write(`events/${id}/status`, "PATCH", { status: value }); await load(); } catch (e) { setError((e as Error).message); } }
  async function importFile() { if (!selectedFile) return; setBusy(true); setError(""); setMessage(""); try { const r = await fetch(apiPath(`import?filename=${encodeURIComponent(selectedFile.name)}`), { method: "POST", credentials: "include", headers: { "X-CSRF-Token": decodeURIComponent(csrf()) }, body: await selectedFile.text() }); if (r.status === 401) { handleUnauthorized(); return; } const d = await r.json(); if (!r.ok) throw new Error(d?.error?.message || "Ошибка импорта"); setMessage(`Импорт завершён: добавлено ${d.added_count}, обновлено ${d.updated_count}, отклонено ${d.rejected_count}`); setSelectedFile(null); if (fileInput.current) fileInput.current.value = ""; } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }

  if (!authorized) return <section className="admin-login-wrap">
    <div className="admin-login-card">
      <span className="admin-eyebrow">Управление проектом</span>
      <h1>Вход в админ-панель</h1>
      <p className="admin-login-copy">Войдите, чтобы управлять событиями и заявками.</p>
      <form className="admin-login-form" onSubmit={login} aria-busy={busy}>
        <label>Логин<input autoComplete="username" value={user} onChange={e => setUser(e.target.value)} required aria-invalid={Boolean(error)} /></label>
        <label>Пароль<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required aria-invalid={Boolean(error)} /></label>
        {error && <p className="admin-alert" role="alert">{error}</p>}
        <button className="button admin-primary" disabled={busy}>{busy ? "Входим…" : "Войти"}</button>
      </form>
    </div>
  </section>;

  return <section className="container admin-page">
    <header className="admin-heading"><div><span className="admin-eyebrow">Управление проектом</span><h1>Админ-панель</h1></div>{role === "admin" && <div className="admin-import"><span>Импорт CSV/JSON</span><input ref={fileInput} id="admin-file" className="admin-file-input" type="file" accept=".csv,.json,application/json,text/csv" onChange={e => { setSelectedFile(e.target.files?.[0] || null); setMessage(""); }} /><div className="admin-import-controls"><button type="button" className="admin-secondary" disabled={busy} onClick={() => fileInput.current?.click()}>Выбрать файл</button><span className="admin-file-name">{selectedFile?.name || "Файл не выбран"}</span><button type="button" className="button" disabled={busy || !selectedFile} onClick={() => void importFile()}>{busy ? "Импортируем…" : "Импортировать"}</button></div></div>}<button type="button" className="admin-secondary" disabled={busy} onClick={() => void logout()}>Выйти</button></header>
    <nav className="admin-tabs" aria-label="Разделы админ-панели">{tabs.filter(([id]) => role === "admin" || id !== "audit").map(([id, label]) => <button className="admin-tab" key={id} onClick={() => setTab(id)} aria-pressed={tab === id}>{label}</button>)}</nav>
    {tab === "events" && <form className="admin-search" onSubmit={e => { e.preventDefault(); void load("events"); }}><input aria-label="Поиск событий" placeholder="Поиск по названию, городу, площадке" value={query} onChange={e => setQuery(e.target.value)} /><button className="button" disabled={busy}>Найти</button></form>}
    {busy && <p className="admin-status" role="status">Загрузка…</p>}{error && <p className="admin-alert" role="alert">{error}</p>}{message && <p className="admin-success" role="status">{message}</p>}
    {!busy && !error && rows.length === 0 && <div className="admin-empty" role="status"><strong>{tab === "events" ? "События не найдены" : tab === "moderation" ? "Нет событий на модерации" : tab === "audit" ? "История пока пуста" : "Заявок пока нет"}</strong><span>{tab === "events" && query ? "Измените запрос и попробуйте снова." : "Здесь появятся новые записи."}</span></div>}
    <div className="admin-records">{rows.map((row, i) => <article className="admin-record" key={row.id || row.created_at || i}>{tab === "events" || tab === "moderation" ? <><strong>{row.title}</strong><span className="admin-record-meta">{[row.city, row.venue, row.status, row.is_disputed ? "спорное" : ""].filter(Boolean).join(" · ")}</span><div className="admin-actions">{["published", "draft", "cancelled"].map(s => <button className="admin-secondary" key={s} disabled={busy} onClick={() => void status(row.id, s)}>{s}</button>)}<details className="admin-details"><summary>Изменить карточку</summary><form className="admin-edit-form" onSubmit={async e => { e.preventDefault(); const f = new FormData(e.currentTarget); try { await write(`events/${row.id}`, "PATCH", { title: f.get("title"), city: f.get("city"), venue: f.get("venue"), description: f.get("description") }); await load(); } catch (x) { setError((x as Error).message); } }}><input name="title" aria-label="Название" defaultValue={row.title} /><input name="city" aria-label="Город" defaultValue={row.city} /><input name="venue" aria-label="Площадка" defaultValue={row.venue} /><textarea name="description" aria-label="Описание" defaultValue={row.description} /><button className="button" disabled={busy}>Сохранить</button></form></details></div></> : tab === "feedback" ? <><strong>{row.status} · {row.created_at}</strong><p>{row.message}</p><div className="admin-actions">{["in_progress", "resolved", "rejected"].map(s => <button className="admin-secondary" disabled={busy} key={s} onClick={async () => { try { await write(`feedback/${row.id}`, "PATCH", { status: s }); await load(); } catch (x) { setError((x as Error).message); } }}>{s}</button>)}</div></> : <><strong>{row.action} · {row.entity_type}/{row.entity_id}</strong><span className="admin-record-meta">{row.actor} · {row.created_at}</span><pre className="admin-json">{JSON.stringify(row.changes, null, 2)}</pre></>}</article>)}</div>
  </section>;
}
