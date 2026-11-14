export function EventMap({ lat, lon, title }: { lat: number | null; lon: number | null; title: string }) {
  if (lat === null || lon === null || !Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  const delta = 0.02;
  const bbox = [lon - delta, lat - delta, lon + delta, lat + delta].join(",");
  const url = `https://www.openstreetmap.org/export/embed.html?bbox=${encodeURIComponent(bbox)}&layer=mapnik&marker=${encodeURIComponent(`${lat},${lon}`)}`;
  return <div className="event-map">
    <iframe title={`Место проведения: ${title}`} src={url} loading="lazy" />
  </div>;
}
