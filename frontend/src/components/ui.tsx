import Link from "next/link";
import {
  ArrowRight,
  Search,
  AlertCircle,
  LoaderCircle,
} from "lucide-react";
import type { CultureEvent } from "@/lib/types";
import { cardTitle } from "@/lib/event-title";
import { RetryButton } from "./retry-button";
export function SearchBar({ defaultValue = "" }: { defaultValue?: string }) {
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
        defaultValue={defaultValue}
      />
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
  return (
    <article className="event-card">
      <div className="event-date" aria-label={validDate ? formatDate(event.date_start) : "Дата уточняется"}><strong>{validDate ? new Intl.DateTimeFormat("ru-RU", { day: "2-digit", timeZone: "Asia/Yekaterinburg" }).format(date) : "—"}</strong><span>{validDate ? new Intl.DateTimeFormat("ru-RU", { month: "short", timeZone: "Asia/Yekaterinburg" }).format(date) : "дата"}</span><small>{validDate ? new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Yekaterinburg" }).format(date) : ""}</small></div>
      <div className="event-card-body">
        <div className="event-meta"><span>{event.category}</span><span>{event.age_limit}+</span></div>
        <h3><Link href={detailHref}>{cardTitle(event.title)}</Link></h3>
        <p className="event-place">{event.city} · {event.venue}</p>
        <div className="event-card-bottom">
          <strong>
            {event.is_free
              ? "Бесплатно"
              : event.price_min !== null
                ? `от ${event.price_min.toLocaleString("ru-RU")} ₽`
                : "Цена уточняется"}
          </strong>
          {(event.status !== "published" || event.is_disputed) &&
            <EventStatusBadge status={event.status} disputed={event.is_disputed} />}
        </div>
        <Link className="event-open" href={detailHref}>Подробнее о событии <ArrowRight size={16} aria-hidden="true" /></Link>
      </div>
    </article>
  );
}
export function formatDate(value: string) {
  // Naive backend timestamps are local to Bashkortostan (UTC+5).
  const date = new Date(
    /[zZ]|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}+05:00`,
  );
  return Number.isNaN(date.getTime())
    ? "Дата уточняется"
    : new Intl.DateTimeFormat("ru-RU", {
        day: "numeric",
        month: "long",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Asia/Yekaterinburg",
      }).format(date);
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
    <div className="state">
      <Search size={30} aria-hidden="true" />
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
