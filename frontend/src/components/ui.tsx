"use client";

import Link from "next/link";
import {
  ArrowRight,
  Search,
  AlertCircle,
  LoaderCircle,
} from "lucide-react";
import type { CultureEvent } from "@/lib/types";
import { cardTitle } from "@/lib/event-title";
import { formatDate } from "@/lib/format-date";
import { RetryButton } from "./retry-button";
import { CategoryIllustration } from "./category-illustration";
import { BashkirCorner, BashkirOrnament } from "./cultural-art";
import { useRef, useState } from "react";
export function SearchBar({ defaultValue = "" }: { defaultValue?: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(defaultValue);
  return (
    <div className="search-field">
      <Search size={21} aria-hidden="true" />
      <label className="sr-only" htmlFor="event-search">
        Название события или ключевые слова
      </label>
      <input
        id="event-search"
        type="search"
        name="query_text"
        placeholder="Название, площадка или тема"
        value={value}
        onChange={(event) => setValue(event.currentTarget.value)}
        ref={input}
      />
      {value && <button className="reset-link" type="button" aria-label="Очистить поиск" onClick={() => { setValue(""); input.current?.form?.requestSubmit(); }}>Очистить</button>}
      <button className="button" type="submit">
        Найти
      </button>
    </div>
  );
}
export function EventStatusBadge({
  status,
  disputed = false,
}: {
  status: string;
  disputed?: boolean;
}) {
  const label = disputed
    ? "Уточняются сведения"
    : { published: "Опубликовано", draft: "Черновик", cancelled: "Отменено" }[
        status
      ] || "Статус уточняется";
  return (
    <span
      className={`status-badge ${disputed || status !== "published" ? "status-warning" : ""}`}
    >
      {label}
    </span>
  );
}
export function EventCard({ event, returnHref }: { event: CultureEvent; returnHref?: string }) {
  const date = new Date(/[zZ]|[+-]\d{2}:\d{2}$/.test(event.date_start) ? event.date_start : `${event.date_start}+05:00`);
  const validDate = !Number.isNaN(date.getTime());
  const detailHref = `/events/${encodeURIComponent(event.id)}${returnHref ? `?returnTo=${encodeURIComponent(returnHref)}` : ""}`;
  const category = event.category?.toLocaleLowerCase("ru-RU") || "другое";
  const posterTone = category.includes("концерт") ? "music" : category.includes("спектакль") ? "theatre" : category.includes("выстав") ? "exhibition" : category.includes("фестивал") ? "festival" : category.includes("кино") ? "cinema" : category.includes("экскурс") ? "excursion" : "other";
  return (
    <article className="event-card">
      <div className="event-date" aria-label={validDate ? formatDate(event.date_start) : "Дата уточняется"}><strong>{validDate ? new Intl.DateTimeFormat("ru-RU", { day: "2-digit", timeZone: "Asia/Yekaterinburg" }).format(date) : "—"}</strong><span>{validDate ? new Intl.DateTimeFormat("ru-RU", { month: "short", timeZone: "Asia/Yekaterinburg" }).format(date) : "дата"}</span><small>{validDate ? new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Yekaterinburg" }).format(date) : ""}</small></div>
      <div className={`event-poster event-poster-${posterTone}`} aria-hidden="true"><span className="event-poster-category">{event.category}</span></div>
      <div className="event-card-body">
        <div className="event-meta"><span className="event-category-meta"><CategoryIllustration tone={posterTone} />{event.category}</span><span>{event.age_limit}+</span></div>
        <h3><Link href={detailHref}>{cardTitle(event.title)}</Link></h3>
        <p className="event-place">{event.city} · {event.venue}</p>
        <div className="event-card-bottom">
          {(event.is_free || event.price_min !== null) && <strong>
            {event.is_free ? "Бесплатно" : `от ${event.price_min?.toLocaleString("ru-RU")} ₽`}
          </strong>}
          {(event.status !== "published" || event.is_disputed) &&
            <EventStatusBadge status={event.status} disputed={event.is_disputed} />}
        </div>
        <Link className="event-open" href={detailHref}>Подробнее о событии <ArrowRight size={16} aria-hidden="true" /></Link>
      </div>
    </article>
  );
}
export function LoadingState() {
  return (
    <div className="state" role="status">
      <LoaderCircle className="animate-spin" size={28} aria-hidden="true" />
      <h2>Загружаем афишу</h2>
      <p>Загружаем события афиши…</p>
    </div>
  );
}
export function ErrorState({
  message = "Не удалось загрузить события.",
  retry,
}: {
  message?: string;
  retry?: () => void;
}) {
  return (
    <div className="state error-state" role="alert">
      <AlertCircle size={30} aria-hidden="true" />
      <h2>Не удалось загрузить данные</h2>
      <p>{message}</p>
      {retry ? (
        <button className="button" onClick={retry}>
          Попробовать снова
        </button>
      ) : (
        <RetryButton />
      )}
    </div>
  );
}
export function EmptyState({ explanation }: { explanation?: string | null }) {
  return (
    <div className="state empty-state">
      <BashkirCorner className="empty-corner" />
      <BashkirOrnament className="empty-ornament" />
      <h2>Пока без событий</h2>
      <p>
        {explanation ||
          "По этому запросу ничего не нашлось. Попробуйте расширить период, выбрать другой город или убрать ограничение цены."}
      </p>
      <Link className="text-link" href="/">
        Посмотреть всю афишу <ArrowRight size={16} />
      </Link>
    </div>
  );
}
