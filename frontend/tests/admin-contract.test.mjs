import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const page = await readFile(new URL("../src/app/admin/page.tsx", import.meta.url), "utf8");
const importHelper = await readFile(new URL("../src/lib/admin-import.ts", import.meta.url), "utf8");

test("admin page exposes event form fields and required browser validation", () => {
  for (const field of ["title", "category", "date_start", "city", "venue", "address", "source_url"])
    assert.match(page, new RegExp(`\\[\\"${field}\\"`), `missing create form field: ${field}`);
  assert.match(page, /required=\{key!=="org_link"\}/);
  assert.match(page, /required value=\{form\.description\}/);
  assert.match(page, /write\("events","POST",form\)/);
});

test("admin page actions match REST event and moderation routes", () => {
  for (const route of ["events/${id}/status", "events/${row.id}", "submissions/${id}"])
    assert.ok(page.includes(route), `missing API route contract ${route}`);
  for (const label of ["Создать событие", "Сохранить", "В архив", "Опубликовать", "Отклонить"])
    assert.ok(page.includes(label), `missing action ${label}`);
  assert.match(page, /fetch\(apiPath\("session"\)/);
  assert.match(page, /headers:\{\"Content-Type\":\"application\/json\",\"X-CSRF-Token\":csrf\(\)\}/);
});

test("moderator navigation hides admin-only sections while retaining moderation", () => {
  assert.match(page, /const adminOnly=\["users","audit","monitoring","settings"\]/);
  assert.match(page, /nav\.filter\(\(\[id\]\)=>role==="admin"\|\|!adminOnly\.includes\(id\)\)/);
  assert.match(page, /\["overview","Обзор"/);
  assert.match(page, /\["moderation","Модерация"/);
});

test("import report renders rejection reasons and preserves byte-level UTF-8 errors", () => {
  assert.match(page, /rejected_reasons/, "the report UI should show rejected rows and reasons");
  assert.match(page, /readImportFile\(selectedFile\)/, "the import path should use strict file decoding");
  assert.match(importHelper, /new TextDecoder\("utf-8",\s*\{\s*fatal:\s*true\s*\}\)/);
});

test("audit view shows actor, timestamp, action, and affected event", () => {
  assert.match(page, /row\.actor/);
  assert.match(page, /row\.created_at/);
  assert.match(page, /row\.action/);
  assert.match(page, /row\.entity_id/);
});
