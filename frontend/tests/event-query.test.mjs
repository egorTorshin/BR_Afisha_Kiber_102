import { test } from "node:test";
import assert from "node:assert/strict";
import { parseEventQuery, eventsHref } from "../src/lib/event-query.ts";

test("all filters survive a shared URL, including zero and Cyrillic", () => {
  const filters = {
    query_text: "Курай & музыка",
    city: "Уфа",
    category: "концерт",
    venue: "Зал №1",
    date_from: "2026-11-13",
    date_to: "2026-12-01",
    max_price: 0,
    max_age_limit: 0,
    is_free_only: true,
    sort: "price_asc",
    page: 3,
    page_size: 24,
    time_after: "18:00",
    time_before: "22:00",
    max_duration_min: 120,
    language: "башкирский",
    tag: "семья",
  };
  const url = new URL(eventsHref(filters), "http://localhost");
  assert.deepEqual(
    parseEventQuery(Object.fromEntries(url.searchParams)),
    filters,
  );
});

test("pagination links change only the page and keep all active conditions", () => {
  const filters = parseEventQuery({
    city: "Уфа",
    max_price: "500",
    page: "2",
    page_size: "1",
    sort: "date_desc",
  });
  const next = new URL(eventsHref({ ...filters, page: 3 }), "http://localhost");
  assert.equal(next.searchParams.get("page"), "3");
  for (const key of ["city", "max_price", "page_size", "sort"])
    assert.equal(next.searchParams.get(key), String(filters[key]));
});

test("unsafe pagination and unknown sorting fall back; duplicate params use first value", () => {
  for (const page of ["0", "-1", "Infinity", "1.5", "9007199254740992"])
    assert.equal(parseEventQuery({ page }).page, 1);
  for (const size of ["0", "101", "NaN"])
    assert.equal(parseEventQuery({ page_size: size }).page_size, 12);
  const parsed = parseEventQuery({
    city: ["Уфа", "Салават"],
    sort: "arbitrary",
    query_text: "  ",
    is_free_only: "false",
  });
  assert.equal(parsed.city, "Уфа");
  assert.equal(parsed.sort, "date_asc");
  assert.equal(parsed.query_text, undefined);
  assert.equal(parsed.is_free_only, undefined);
  assert.equal(eventsHref(), "/events");
  assert.equal(
    eventsHref({ city: "", max_price: 0, is_free_only: false }),
    "/events?max_price=0",
  );
});
