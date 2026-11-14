"use client";
import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

const categories = ["концерт", "спектакль", "выставка", "фестиваль", "кино", "экскурсия", "другое"];

export function EventSubmissionForm() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    const free = data.has("is_free");
    const input: Record<string, string | number | boolean | null> = {
      title: String(data.get("title") || "").trim(),
      category: String(data.get("category") || ""),
      date_start: String(data.get("date_start") || ""),
      date_end: String(data.get("date_end") || "") || null,
      city: String(data.get("city") || "").trim(),
      venue: String(data.get("venue") || "").trim(),
      address: String(data.get("address") || "").trim(),
      description: String(data.get("description") || "").trim(),
      source_url: String(data.get("source_url") || "").trim(),
      org_link: String(data.get("org_link") || "").trim() || null,
      is_free: free,
      price_min: free || !data.get("price_min") ? null : Number(data.get("price_min")),
      price_max: free || !data.get("price_max") ? null : Number(data.get("price_max")),
      age_limit: Number(data.get("age_limit") || 0),
    };
    try {
      await api.submitEvent(input);
      setSubmitted(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось отправить заявку");
    } finally {
      setBusy(false);
    }
  }

  if (submitted) return <div className="submission-success" role="status"><h2>Заявка отправлена</h2><p>Событие появится в афише после проверки редактором.</p><Link className="button" href="/">Вернуться к афише</Link></div>;
  return <form className="submission-form" onSubmit={submit}>
    <div className="submission-grid">
      <label className="submission-wide">Название<input name="title" required minLength={2} maxLength={250} autoComplete="off" /></label>
      <label>Категория<select name="category" required>{categories.map(category => <option key={category} value={category}>{category[0].toUpperCase() + category.slice(1)}</option>)}</select></label>
      <label>Возраст<select name="age_limit">{[0, 6, 12, 16, 18].map(age => <option key={age} value={age}>{age}+</option>)}</select></label>
      <label>Начало<input name="date_start" type="datetime-local" required /></label>
      <label>Окончание, если есть<input name="date_end" type="datetime-local" /></label>
      <label>Город<input name="city" required minLength={2} maxLength={120} /></label>
      <label>Площадка<input name="venue" required minLength={2} maxLength={250} /></label>
      <label className="submission-wide">Адрес<input name="address" required minLength={2} maxLength={500} /></label>
      <label className="submission-wide">Описание<textarea name="description" required minLength={20} maxLength={10000} rows={7} /></label>
      <label className="submission-wide">Страница события у организатора<input name="source_url" type="url" required maxLength={2048} placeholder="https://" /></label>
      <label className="submission-wide">Ссылка на билеты или организатора, если есть<input name="org_link" type="url" maxLength={2048} placeholder="https://" /></label>
      <label className="submission-check"><input name="is_free" type="checkbox" /> Бесплатно</label>
      <div className="submission-prices"><label>Цена от, ₽<input name="price_min" type="number" min="0" step="1" /></label><label>Цена до, ₽<input name="price_max" type="number" min="0" step="1" /></label></div>
    </div>
    <p>Проверьте дату, стоимость и ссылку перед отправкой. Заявка не публикуется автоматически.</p>
    {error && <p className="form-error" role="alert">{error}</p>}
    <button className="button" type="submit" disabled={busy}>{busy ? "Отправляем…" : "Отправить на модерацию"}</button>
  </form>;
}
