import { test } from "node:test";
import assert from "node:assert/strict";
import { geocodeQuery } from "../src/lib/geocode-query.ts";

test("regional addresses contain one city and the Bashkortostan region", () => {
  assert.equal(geocodeQuery("г. Стерлитамак, ул. Коммунистическая, 101", "Стерлитамак"),
    "улица Коммунистическая, 101, Стерлитамак, Республика Башкортостан, Россия");
  assert.equal(geocodeQuery("г Мелеуз, ул Ленина, 123", "Мелеуз"),
    "улица Ленина, 123, Мелеуз, Республика Башкортостан, Россия");
  assert.equal(geocodeQuery("Респ. Башкортостан, г. Благовещенск, ул. Седова, 98а", "Благовещенск"),
    "улица Седова, 98а, Благовещенск, Республика Башкортостан, Россия");
});

test("empty address never resolves to an imprecise city marker", () => {
  assert.equal(geocodeQuery("", "Стерлитамак"), null);
});
