"use client";
import { useMemo, useState } from "react";

export default function Developers() {
  const [city, setCity] = useState("");
  const [category, setCategory] = useState("");
  const [period, setPeriod] = useState("week");
  const [height, setHeight] = useState("480");
  const src = useMemo(() => {
    const url = new URLSearchParams();
    if (city.trim()) url.set("city", city.trim());
    if (category.trim()) url.set("category", category.trim());
    url.set("period", period);
    url.set("height", height);
    return `/embed?${url.toString()}`;
  }, [city, category, period, height]);
  const snippet = `<iframe src="https://ваш-домен.ru${src}" width="100%" height="${height}" title="Афиша мероприятий" loading="lazy" style="border:0;max-width:100%"></iframe>`;
  return <div className="container page-content">
    <p className="eyebrow">ДЛЯ ПАРТНЁРОВ</p><h1 className="page-title">Виджет афиши</h1>
    <p className="page-intro">Настройте подборку и вставьте её на свой сайт. Виджет загружает свежие события и работает в отдельном iframe.</p>
    <section className="widget-builder">
      <form className="widget-controls" onSubmit={e => e.preventDefault()}>
        <label>Город<input value={city} onChange={e => setCity(e.target.value)} placeholder="Например, Уфа" maxLength={100}/></label>
        <label>Категория<input value={category} onChange={e => setCategory(e.target.value)} placeholder="Например, Концерт" maxLength={100}/></label>
        <label>Период<select value={period} onChange={e => setPeriod(e.target.value)}><option value="week">Ближайшая неделя</option><option value="month">Ближайший месяц</option><option value="all">Без ограничения</option></select></label>
        <label>Высота, px<input type="number" min="240" max="1000" step="20" value={height} onChange={e => setHeight(e.target.value)} /></label>
      </form>
      <h2>Предпросмотр</h2>
      <div className="widget-preview"><iframe src={src} title="Предпросмотр виджета афиши" width="100%" height={Math.min(600, Math.max(240, Number(height) || 480))} loading="lazy" /></div>
      <label className="embed-code-label" htmlFor="embed-code">HTML-код для вставки</label>
      <textarea id="embed-code" className="embed-code" readOnly value={snippet} onFocus={e => e.currentTarget.select()} />
      <button className="button" type="button" onClick={async e => { const actual = snippet.replace("https://ваш-домен.ru", window.location.origin); try { await navigator.clipboard.writeText(actual); e.currentTarget.textContent = "Скопировано"; } catch { const area = document.getElementById("embed-code") as HTMLTextAreaElement | null; if (area) { area.value = actual; area.focus(); area.select(); } } }}>Скопировать код</button>
      <p className="results-note">Если предпросмотр открыт с localhost, замените адрес в коде на публичный URL сайта после публикации.</p>
    </section>
  </div>;
}
