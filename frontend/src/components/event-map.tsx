import { geocodeEventAddress } from "@/lib/geocode";

export async function EventMap({ lat, lon, title, city, address }: {
  lat: number | null; lon: number | null; title: string; city: string; address: string;
}) {
  const storedLocation = lat !== null && lon !== null && Number.isFinite(lat) && Number.isFinite(lon) &&
    Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? { lat, lon } : null;
  const location = storedLocation || await geocodeEventAddress(address, city);
  if (!location && !address) return null;

  const delta = 0.008;
  const bbox = location && [location.lon - delta, location.lat - delta, location.lon + delta, location.lat + delta].join(",");
  const url = location && `https://www.openstreetmap.org/export/embed.html?bbox=${encodeURIComponent(bbox || "")}&layer=mapnik&marker=${encodeURIComponent(`${location.lat},${location.lon}`)}`;

  return <section className="event-map" aria-labelledby="event-map-heading">
    <div className="event-map-heading">
      <div><span className="issue-label">Место проведения</span><h2 id="event-map-heading">На карте</h2></div>
      <p>{[title, city, address].filter(Boolean).join(" · ")}</p>
    </div>
    {url
      ? <iframe title={`Карта: ${title}`} src={url} loading="eager" referrerPolicy="strict-origin-when-cross-origin" />
      : <p className="event-map-unavailable">Не удалось определить точное место по адресу. Проверьте адрес у организатора.</p>}
  </section>;
}
