import Link from "next/link";
import {
  ArrowRight,
  CalendarDays,
  MapPin,
  Search,
  AlertCircle,
  LoaderCircle,
} from "lucide-react";
import type { CultureEvent } from "@/lib/types";
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
        placeholder="Что вам хочется увидеть?"
        defaultValue={defaultValue}
      />
      <button className="button" type="submit">
        Найти <ArrowRight size={17} aria-hidden="true" />
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
  return (
    <article className="event-card">
      <div className="event-art" aria-hidden="true">
        <span>✳</span>
        <span className="art-category">{event.category}</span>
      </div>
      <div className="event-card-body">
        <div className="event-meta">
          <span>{event.category}</span>
          <span>{event.age_limit}+</span>
        </div>
        <h3>
          <Link href={`/events/${encodeURIComponent(event.id)}${returnHref ? `?returnTo=${encodeURIComponent(returnHref)}` : ""}`}>
            {event.title}
          </Link>
        </h3>
        <p>
          <CalendarDays size={16} aria-hidden="true" />
          {formatDate(event.date_start)}
        </p>
        <p>
          <MapPin size={16} aria-hidden="true" />
          {event.city} · {event.venue}
        </p>
        <div className="event-card-bottom">
          <strong>
            {event.is_free
              ? "Бесплатно"
              : event.price_min !== null
                ? `от ${event.price_min.toLocaleString("ru-RU")} ₽`
                : "Цена уточняется"}
          </strong>
          <EventStatusBadge
            status={event.status}
            disputed={event.is_disputed}
          />
        </div>
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
      <h2>Собираем впечатления</h2>
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
      <h2>Афиша немного задерживается</h2>
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
      <Link className="text-link" href="/events">
        Посмотреть всю афишу <ArrowRight size={16} />
      </Link>
    </div>
  );
}
