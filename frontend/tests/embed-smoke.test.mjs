import assert from "node:assert/strict";
import { test } from "node:test";

const origin = process.env.EMBED_SMOKE_ORIGIN || "http://127.0.0.1:3000";
const params = new URLSearchParams({
  city: "Уфа",
  category: "концерт",
  period: "month",
  height: "360",
});
const widgetUrl = `${origin}/embed?${params}`;

test("standalone 360px iframe host points to the parameterized widget", () => {
  const host = `<!doctype html><meta charset="utf-8"><iframe title="Афиша культуры Башкортостана" src="${widgetUrl}" width="360" height="360" style="width:360px;max-width:100%;border:0"></iframe>`;
  assert.match(host, /<iframe\b/);
  assert.match(host, /width="360"/);
  assert.match(host, /city=%D0%A3%D1%84%D0%B0/);
  assert.match(host, /category=%D0%BA%D0%BE%D0%BD%D1%86%D0%B5%D1%80%D1%82/);
  assert.match(host, /period=month/);
  assert.match(host, /height=360/);
});

test("widget serves inside an iframe with requested dimensions and parameters", async () => {
  const response = await fetch(widgetUrl);
  const html = await response.text();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("x-frame-options"), null);
  assert.match(html, /height:360px/);
  assert.match(html, /АФИША/);
  assert.doesNotMatch(html, /Некорректное значение: city|period должен быть/);
});

test("widget rejects unsupported height values", async () => {
  const response = await fetch(`${origin}/embed?height=239`);
  const html = await response.text();
  assert.equal(response.status, 200);
  assert.match(html, /height должен быть от 240 до 1000 пикселей/);
});
