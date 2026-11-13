import type { EventFilters } from "./types";

export type SearchParams = Record<string, string | string[] | undefined>;

/** URL transport only; event search and validation belong to FastAPI. */
export function parseEventQuery(params: SearchParams): EventFilters {
  const filters: EventFilters = {};
  const get = (key: string) => {
    const value = params[key];
    return (Array.isArray(value) ? value[0] : value)?.trim() || undefined;
  };
  for (const key of [
    "query_text",
    "city",
    "category",
    "venue",
    "date_from",
    "date_to",
    "time_after",
    "time_before",
    "language",
    "tag",
  ] as const) {
    filters[key] = get(key);
  }
  for (const key of [
    "max_price",
    "max_age_limit",
    "max_duration_min",
  ] as const) {
    const value = get(key);
    if (value !== undefined && Number.isSafeInteger(Number(value)))
      filters[key] = Number(value);
  }
  const page = Number(get("page"));
  const size = Number(get("page_size"));
  filters.page = Number.isSafeInteger(page) && page > 0 ? page : 1;
  filters.page_size =
    Number.isSafeInteger(size) && size > 0 && size <= 100 ? size : 12;
  const sort = get("sort");
  filters.sort =
    sort === "date_desc" || sort === "price_asc" ? sort : "date_asc";
  if (get("is_free_only") === "true" || get("is_free_only") === "1")
    filters.is_free_only = true;
  return filters;
}

export function eventsHref(filters: EventFilters = {}): string {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== "" && value !== false)
      params.set(key, String(value));
  });
  return `/events${params.size ? `?${params}` : ""}`;
}
