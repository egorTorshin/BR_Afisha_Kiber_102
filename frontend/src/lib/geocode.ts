import "server-only";

export type MapLocation = { lat: number; lon: number };

const resolved = new Map<string, MapLocation | null>();
const pending = new Map<string, Promise<MapLocation | null>>();
let queue = Promise.resolve();
let nextRequestAt = 0;

function validLocation(lat: number, lon: number): MapLocation | null {
  return Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180
    ? { lat, lon } : null;
}

async function lookup(address: string, city: string): Promise<MapLocation | null> {
  const origin = (process.env.MAP_GEOCODER_URL || "https://nominatim.openstreetmap.org").replace(/\/$/, "");
  const url = new URL(`${origin}/search`);
  const cityName = city.trim().toLowerCase();
  const street = address.split(",").map((part) => part.trim()).filter((part) =>
    part && !/^\d{6}$/.test(part) && part.toLowerCase() !== cityName && part.toLowerCase() !== "россия").join(", ");
  url.searchParams.set("q", [street, city, "Россия"].filter(Boolean).join(", "));
  url.searchParams.set("format", "jsonv2");
  url.searchParams.set("limit", "1");
  url.searchParams.set("countrycodes", "ru");
  url.searchParams.set("addressdetails", "1");

  // Nominatim permits at most one request per second for the whole application.
  const previous = queue;
  let release!: () => void;
  queue = new Promise<void>((resolve) => { release = resolve; });
  await previous;
  const delay = Math.max(0, nextRequestAt - Date.now());
  if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
  nextRequestAt = Date.now() + 1100;
  release();

  const response = await fetch(url, {
    headers: { "User-Agent": "AfishaBashkortostana-EventMap/1.0", "Accept-Language": "ru" },
    cache: "force-cache",
    next: { revalidate: 60 * 60 * 24 * 30 },
    signal: AbortSignal.timeout(6000),
  });
  if (!response.ok) throw new Error(`Geocoder returned ${response.status}`);
  const results: unknown = await response.json();
  if (!Array.isArray(results) || !results.length) return null;
  const first = results[0] as { lat?: string; lon?: string };
  return validLocation(Number(first.lat), Number(first.lon));
}

export async function geocodeEventAddress(address: string, city: string): Promise<MapLocation | null> {
  const key = `${address.trim().toLowerCase()}|${city.trim().toLowerCase()}`;
  if (!address.trim()) return null;
  if (resolved.has(key)) return resolved.get(key) ?? null;
  const existing = pending.get(key);
  if (existing) return existing;
  const request = lookup(address, city).then((location) => {
    resolved.set(key, location);
    return location;
  }).catch(() => null).finally(() => pending.delete(key));
  pending.set(key, request);
  return request;
}
