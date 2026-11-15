import { test } from "node:test";
import assert from "node:assert/strict";
import { readImportFile, readImportResponse } from "../src/lib/admin-import.ts";

function file(name, value) {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  return { name, size: bytes.byteLength, arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) };
}

test("reads valid CSV and JSON file bytes as UTF-8", async () => {
  assert.equal(await readImportFile(file("events.csv", "title,city\nСобытие,Уфа")), "title,city\nСобытие,Уфа");
  assert.equal(await readImportFile(file("events.JSON", '[{"title":"Событие"}]')), '[{"title":"Событие"}]');
});

test("rejects invalid UTF-8 and empty files with clear messages", async () => {
  await assert.rejects(readImportFile(file("bad.csv", Uint8Array.from([0x74, 0x69, 0x74, 0x6c, 0x65, 0xff]))), /некорректный кодировки|некорректную кодировку/i);
  await assert.rejects(readImportFile(file("empty.json", "  \n")), /Файл пустой/);
  await assert.rejects(readImportFile(file("empty.csv", "")), /Файл пустой/);
});

test("preserves import report counts and rejected row reasons", async () => {
  const report = { added_count: 2, updated_count: 1, rejected_count: 1,
    rejected_reasons: [{ row: 4, title: "Без даты", reason: "Некорректная дата" }] };
  assert.deepEqual(await readImportResponse({ ok: true, status: 200, json: async () => report }), report);
});

test("shows safe API errors without exposing traceback", async () => {
  await assert.rejects(readImportResponse({ ok: false, status: 422, json: async () => ({ detail: "Некорректная кодировка" }) }), /Некорректная кодировка/);
  await assert.rejects(readImportResponse({ ok: false, status: 500, json: async () => ({ detail: "Traceback (most recent call last): secret" }) }), /HTTP 500/);
  await assert.rejects(readImportResponse({ ok: false, status: 502, json: async () => { throw new Error("invalid JSON"); } }), /HTTP 502/);
});
