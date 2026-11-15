import { test } from "node:test";
import assert from "node:assert/strict";
import { parseEventQuery, eventsHref } from "../src/lib/event-query.ts";
import { cardTitle } from "../src/lib/event-title.ts";

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
  assert.equal(eventsHref(), "/");
  assert.equal(
    eventsHref({ city: "", max_price: 0, is_free_only: false }),
    "/?max_price=0",
  );
});

test("card titles omit only a trailing edition number", () => {
  assert.equal(cardTitle("Концерт в Уфе (выпуск №51)"), "Концерт в Уфе");
  assert.equal(cardTitle("Выставка (выпуск №3) в Уфе"), "Выставка (выпуск №3) в Уфе");
});

test("malformed numeric filters are omitted and URL values are encoded", () => {
  const filters = parseEventQuery({
    max_price: "не число",
    max_duration_min: "2.5",
    city: "Уфа & Салават",
    query_text: "Курай концерт",
  });
  assert.equal(filters.max_price, undefined);
  assert.equal(filters.max_duration_min, undefined);
  const url = new URL(eventsHref(filters), "http://localhost");
  assert.equal(url.searchParams.get("city"), "Уфа & Салават");
  assert.equal(url.searchParams.get("query_text"), "Курай концерт");
  assert.equal(filters.page, 1);
  assert.equal(filters.page_size, 12);
});

test("catalog search submission stores the title query and starts at page one", () => {
  const submitted = new URL(eventsHref({ query_text: "Курай концерт", city: "Уфа", page: 1, page_size: 24, sort: "price_asc" }), "http://localhost");
  assert.equal(submitted.searchParams.get("query_text"), "Курай концерт");
  assert.equal(submitted.searchParams.get("page"), "1");
  assert.equal(parseEventQuery(Object.fromEntries(submitted.searchParams)).query_text, "Курай концерт");
});

test("catalog search restores from URL and combines with filters", () => {
  const filters = parseEventQuery({ query_text: "театр", city: "Уфа", category: "спектакль", page: "4", max_price: "500" });
  assert.equal(filters.query_text, "театр");
  const submitted = new URL(eventsHref({ ...filters, page: 1 }), "http://localhost");
  assert.equal(submitted.searchParams.get("query_text"), "театр");
  assert.equal(submitted.searchParams.get("city"), "Уфа");
  assert.equal(submitted.searchParams.get("category"), "спектакль");
  assert.equal(submitted.searchParams.get("max_price"), "500");
  assert.equal(submitted.searchParams.get("page"), "1");
});

test("clearing the catalog query removes it while retaining other filters", () => {
  const filters = parseEventQuery({ query_text: "театр", city: "Уфа", page: "3" });
  const cleared = new URL(eventsHref({ ...filters, query_text: "", page: 1 }), "http://localhost");
  assert.equal(cleared.searchParams.has("query_text"), false);
  assert.equal(cleared.searchParams.get("city"), "Уфа");
  assert.equal(cleared.searchParams.get("page"), "1");
});
