import Link from "next/link";
import { eventsHref } from "@/lib/event-query";
import { api } from "@/lib/api";
import { EmptyState, ErrorState, EventCard } from "./ui";
import type { EventFilters } from "@/lib/types";
export async function EventPreview({
  filters = {},
  limit = 3,
}: {
  filters?: EventFilters;
  limit?: number;
}) {
  let result;
  try {
    result = await api.events({ ...filters, page_size: limit });
  } catch (error) {
    return (
      <ErrorState
        message={error instanceof Error ? error.message : undefined}
      />
    );
  }
  return (
    <>
      {result.events.length ? (
        <div className="event-grid">
          {result.events.map((event) => (
            <EventCard key={event.id} event={event} />
          ))}
        </div>
      ) : (
        <EmptyState />
      )}
      {result.total > limit && (
        <p className="results-note">
          Показаны первые {limit} из {result.total} событий.{" "}
          <Link className="text-link" href={eventsHref(filters)}>
            Посмотреть все события →
          </Link>
        </p>
      )}
    </>
  );
}
