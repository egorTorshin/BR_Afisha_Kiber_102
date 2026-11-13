import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { eventsHref } from "@/lib/event-query";
import type { EventFilters, EventPage } from "@/lib/types";
import { EmptyState, ErrorState, EventCard } from "./ui";

function Pagination({
  result,
  filters,
}: {
  result: EventPage;
  filters: EventFilters;
}) {
  if (result.pages <= 1) return null;
  const pages = [
    ...new Set([
      1,
      result.page - 1,
      result.page,
      result.page + 1,
      result.pages,
    ]),
  ]
    .filter((p) => p >= 1 && p <= result.pages)
    .sort((a, b) => a - b);
  const href = (page: number) =>
    eventsHref({ ...filters, page, page_size: result.page_size }) + "#results";
  return (
    <nav className="pagination" aria-label="Страницы мероприятий">
      {result.page > 1 && (
        <Link href={href(result.page - 1)} rel="prev">
          ← Назад
        </Link>
      )}
      {pages.map((page, i) => (
        <span key={page} className="page-item">
          {i > 0 && page - pages[i - 1] > 1 && (
            <span aria-hidden="true">…</span>
          )}
          <Link
            href={href(page)}
            aria-label={`Страница ${page}`}
            aria-current={page === result.page ? "page" : undefined}
          >
            {page}
          </Link>
        </span>
      ))}
      {result.page < result.pages && (
        <Link href={href(result.page + 1)} rel="next">
          Далее →
        </Link>
      )}
    </nav>
  );
}

export async function EventResults({ filters }: { filters: EventFilters }) {
  let result;
  try {
    result = await api.events(filters);
  } catch (error) {
    return (
      <>
        {error instanceof ApiError && error.status === 422 && (
          <p className="results-note">
            Проверьте даты и числовые ограничения в фильтрах. Начало периода
            должно быть не позже окончания.
          </p>
        )}
        <ErrorState
          message={error instanceof Error ? error.message : undefined}
        />
      </>
    );
  }
  if (result.pages > 0 && result.page > result.pages)
    return (
      <div className="state" role="status">
        <h2>Такой страницы пока нет</h2>
        <p>
          В этой выдаче страниц: {result.pages}. Возможно, состав афиши
          изменился.
        </p>
        <Link className="button" href={eventsHref({ ...filters, page: 1 })}>
          К первой странице
        </Link>
      </div>
    );
  return (
    <>
      <div className="preview-label" role="status">
        <span>Найдено событий: {result.total}</span>
        {result.total > 0 && (
          <span>
            Страница {result.page} из {result.pages} · Показано{" "}
            {(result.page - 1) * result.page_size + 1}–
            {Math.min(result.page * result.page_size, result.total)}
          </span>
        )}
      </div>
      {result.events.length ? (
        <div className="event-grid">
            {result.events.map((event) => (
            <EventCard key={event.id} event={event} returnHref={eventsHref({ ...filters, page: result.page, page_size: result.page_size })} />
          ))}
        </div>
      ) : (
        <EmptyState explanation={result.explanation} />
      )}
      <Pagination result={result} filters={filters} />
    </>
  );
}
