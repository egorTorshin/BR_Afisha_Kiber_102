"use client";

import Form from "next/form";
import Link from "next/link";
import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { SearchBar } from "./ui";
import { eventsHref } from "@/lib/event-query";
import type { CategoryItem, CityItem, EventFilters, VenueItem } from "@/lib/types";

type MenuName = "city" | "date" | "category" | "advanced" | null;
const dateValue = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const midnight = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate());

function Calendar({ from, to, onChange, close }: { from: string; to: string; onChange: (from: string, to: string) => void; close: () => void }) {
  const [month, setMonth] = useState(() => {
    const d = from ? new Date(`${from}T12:00:00`) : new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1);
  });
  const start = new Date(month.getFullYear(), month.getMonth(), 1);
  const offset = (start.getDay() + 6) % 7;
  const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const pick = (value: string) => {
    if (!from || to || value < from) onChange(value, "");
    else { onChange(from, value); close(); }
  };
  const quick = (kind: "today" | "tomorrow" | "weekend") => {
    const today = midnight(new Date());
    if (kind === "today") onChange(dateValue(today), dateValue(today));
    else if (kind === "tomorrow") { const next = new Date(today); next.setDate(next.getDate() + 1); onChange(dateValue(next), dateValue(next)); }
    else { const saturday = new Date(today); const day = today.getDay(); const delta = day === 0 ? -1 : day === 6 ? 0 : 6 - day; saturday.setDate(today.getDate() + delta); const sunday = new Date(saturday); sunday.setDate(sunday.getDate() + 1); onChange(dateValue(saturday), dateValue(sunday)); }
    close();
  };
  return <div className="calendar-popover">
    <div className="calendar-quick">{([["Сегодня", "today"], ["Завтра", "tomorrow"], ["Выходные", "weekend"]] as const).map(([label, key]) => <button type="button" key={key} onClick={() => quick(key)}>{label}</button>)}</div>
    <div className="calendar-heading"><button type="button" aria-label="Предыдущий месяц" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}>‹</button><strong>{new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric" }).format(month)}</strong><button type="button" aria-label="Следующий месяц" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}>›</button></div>
    <div className="calendar-grid" aria-label="Выберите даты"><span>Пн</span><span>Вт</span><span>Ср</span><span>Чт</span><span>Пт</span><span>Сб</span><span>Вс</span>{Array.from({ length: offset }, (_, i) => <i key={`blank-${i}`} />)}{Array.from({ length: days }, (_, i) => { const d = new Date(month.getFullYear(), month.getMonth(), i + 1); const value = dateValue(d); const selected = value === from || value === to || (from && to && value > from && value < to); return <button type="button" aria-pressed={Boolean(selected)} className={selected ? "selected" : ""} key={value} onClick={() => pick(value)}>{i + 1}</button>; })}</div>
    <p className="calendar-hint">{from && !to ? "Выберите дату окончания" : "Выберите дату начала и окончания"}</p>
  </div>;
}

function FilterChip({ children, active, onClick, expanded }: { children: React.ReactNode; active?: boolean; onClick: (event: ReactMouseEvent<HTMLButtonElement>) => void; expanded: boolean }) {
  return <button className={`filter-trigger${active ? " is-active" : ""}`} type="button" aria-expanded={expanded} onClick={onClick}>{children}<span aria-hidden="true">⌄</span></button>;
}

function FilterChips({ filters }: { filters: EventFilters }) {
  const labels: [keyof EventFilters, string][] = [["query_text", "Поиск"], ["city", "Город"], ["category", "Категория"], ["date_from", "Дата с"], ["date_to", "Дата по"], ["venue", "Площадка"], ["max_price", "Цена до"], ["max_age_limit", "Возраст до"], ["is_free_only", "Бесплатные"]];
  const active = labels.filter(([key]) => filters[key] !== undefined && filters[key] !== false && filters[key] !== "");
  if (!active.length) return null;
  return <div className="active-filters" aria-label="Применённые фильтры"><span className="active-filters-label">Фильтры:</span>{active.map(([key, label]) => { const value = filters[key]; const text = key === "is_free_only" ? "Бесплатные" : `${label}: ${String(value)}`; return <Link className="filter-chip" key={key} href={eventsHref({ ...filters, [key]: undefined, page: 1 })} aria-label={`Удалить фильтр: ${text}`}>{text}<span aria-hidden="true">×</span></Link>; })}<Link className="reset-link" href="/events">Сбросить всё</Link></div>;
}

export function CatalogForm({ filters, cities, categories }: { filters: EventFilters; cities: CityItem[]; categories: CategoryItem[]; venues: VenueItem[] }) {
  const [open, setOpen] = useState<MenuName>(null);
  const [city, setCity] = useState(filters.city || "");
  const [category, setCategory] = useState(filters.category || "");
  const [from, setFrom] = useState(filters.date_from || "");
  const [to, setTo] = useState(filters.date_to || "");
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => { const key = (event: KeyboardEvent) => { if (event.key === "Escape" && open) { setOpen(null); trigger.current?.focus(); } }; document.addEventListener("keydown", key); return () => document.removeEventListener("keydown", key); }, [open]);
  useEffect(() => { if (!open) return; const outside = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(null); }; document.addEventListener("mousedown", outside); return () => document.removeEventListener("mousedown", outside); }, [open]);
  const toggle = (name: Exclude<MenuName, null>, button: HTMLButtonElement) => { trigger.current = button; setOpen(open === name ? null : name); };
  const advancedActive = Boolean(filters.venue || filters.max_price !== undefined || filters.max_age_limit !== undefined || filters.is_free_only);
  return <>
    <Form action="/events" className="catalog-form" aria-label="Фильтры мероприятий">
      <SearchBar defaultValue={filters.query_text} />
      <div className="filter-controls" ref={root}>
        <div className="filter-triggers">
          <FilterChip active={Boolean(city)} expanded={open === "city"} onClick={(e) => toggle("city", e.currentTarget)}>{city || "Город"}</FilterChip>
          <FilterChip active={Boolean(from || to)} expanded={open === "date"} onClick={(e) => toggle("date", e.currentTarget)}>{from ? `${from}${to ? ` — ${to}` : ""}` : "Когда"}</FilterChip>
          <FilterChip active={Boolean(category)} expanded={open === "category"} onClick={(e) => toggle("category", e.currentTarget)}>{category || "Категория"}</FilterChip>
          <FilterChip active={advancedActive} expanded={open === "advanced"} onClick={(e) => toggle("advanced", e.currentTarget)}>Дополнительно</FilterChip>
        </div>
        {open === "city" && <div className="filter-popover choice-popover" role="dialog" aria-label="Выберите город"><p className="popover-title">Город</p><button type="button" className={!city ? "choice selected" : "choice"} onClick={() => { setCity(""); setOpen(null); }}>Все города</button>{cities.map((item) => <button type="button" className={city === item.city ? "choice selected" : "choice"} key={item.city} onClick={() => { setCity(item.city); setOpen(null); }}>{item.city}<small>{item.cnt}</small></button>)}</div>}
        {open === "category" && <div className="filter-popover choice-popover" role="dialog" aria-label="Выберите категорию"><p className="popover-title">Категория</p><button type="button" className={!category ? "choice selected" : "choice"} onClick={() => { setCategory(""); setOpen(null); }}>Все направления</button>{categories.map((item) => <button type="button" className={category === item.category ? "choice selected" : "choice"} key={item.category} onClick={() => { setCategory(item.category); setOpen(null); }}>{item.category}<small>{item.cnt}</small></button>)}</div>}
        {open === "date" && <div className="filter-popover date-popover" role="dialog" aria-label="Выберите даты"><Calendar from={from} to={to} onChange={(a, b) => { setFrom(a); setTo(b); }} close={() => setOpen(null)} /><button type="button" className="clear-date" onClick={() => { setFrom(""); setTo(""); }}>Очистить даты</button></div>}
        {open === "advanced" && <div className="filter-popover advanced-panel" role="dialog" aria-label="Дополнительные фильтры"><div className="advanced-heading"><p className="popover-title">Дополнительные фильтры</p><button type="button" aria-label="Закрыть дополнительные фильтры" onClick={() => setOpen(null)}>×</button></div><label>Площадка<input name="venue" defaultValue={filters.venue} placeholder="Название площадки" /></label><label>Цена до, ₽<input type="number" name="max_price" min="0" step="1" defaultValue={filters.max_price} placeholder="Без ограничения" /></label><label>Возраст до<input type="number" name="max_age_limit" min="0" max="18" step="1" defaultValue={filters.max_age_limit} placeholder="Любой (0–18)" /></label><label className="checkbox-label"><input type="checkbox" name="is_free_only" value="true" defaultChecked={filters.is_free_only} />Только бесплатные</label></div>}
      </div>
      <input type="hidden" name="city" value={city} /><input type="hidden" name="category" value={category} /><input type="hidden" name="date_from" value={from} /><input type="hidden" name="date_to" value={to} />
      <input type="hidden" name="sort" value={filters.sort || "date_asc"} /><input type="hidden" name="page_size" value={filters.page_size || 12} />
      {(["time_after", "time_before", "max_duration_min", "language", "tag"] as const).map((key) => filters[key] !== undefined ? <input key={key} type="hidden" name={key} value={filters[key]} /> : null)}
      <button className="button filter-apply" type="submit">Показать события</button>
    </Form>
    <FilterChips filters={filters} />
  </>;
}

export function ResultsToolbar({ filters }: { filters: EventFilters }) {
  const preserved = Object.entries(filters).filter(([key, value]) => !["sort", "page_size", "page"].includes(key) && value !== undefined && value !== false);
  const base = Object.fromEntries(preserved) as EventFilters;
  const sorts = [["date_asc", "Сначала ближайшие"], ["date_desc", "Сначала поздние"], ["price_asc", "Сначала дешевле"]] as const;
  const activeSort = sorts.find(([value]) => value === filters.sort)?.[1] || sorts[0][1];
  return <div className="results-toolbar" aria-label="Настройки выдачи"><span>Результаты афиши</span><div className="toolbar-options"><details className="toolbar-menu"><summary>Сортировка: <strong>{activeSort}</strong></summary><div className="toolbar-popover">{sorts.map(([value, label]) => <Link key={value} href={eventsHref({ ...base, sort: value, page: 1 })} aria-current={filters.sort === value ? "true" : undefined}>{label}</Link>)}</div></details><details className="toolbar-menu"><summary>На странице: <strong>{filters.page_size || 12}</strong></summary><div className="toolbar-popover">{[...new Set([12, 24, 48, filters.page_size || 12])].sort((a,b) => a-b).map((size) => <Link key={size} href={eventsHref({ ...base, sort: filters.sort, page_size: size, page: 1 })} aria-current={filters.page_size === size ? "true" : undefined}>{size} событий</Link>)}</div></details></div></div>;
}
