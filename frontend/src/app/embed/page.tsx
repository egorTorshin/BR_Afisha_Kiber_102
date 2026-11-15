import { api, ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format-date";
import type { EventFilters } from "@/lib/types";
import { cardTitle } from "@/lib/event-title";
import styles from "./widget.module.css";

export const dynamic = "force-dynamic";
type Params = Record<string, string | string[] | undefined>;
const periods = ["week", "month", "all"] as const;
function one(value: string | string[] | undefined) { return Array.isArray(value) ? value[0] : value; }
function bashkortostanDate(daysAhead = 0) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Yekaterinburg",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((part) => part.type === type)?.value);
  const date = new Date(Date.UTC(value("year"), value("month") - 1, value("day") + daysAhead));
  return date.toISOString().slice(0, 10);
}
function validText(value: string | undefined, label: string) {
  if (!value) return undefined;
  const text = value.trim();
  if (text.length > 100 || /[<>\u0000-\u001f]/.test(text)) throw new Error(`Некорректное значение: ${label}`);
  return text || undefined;
}
function filtersFor(params: Params): { filters: EventFilters; height: number } {
  const city = validText(one(params.city), "city");
  const category = validText(one(params.category), "category");
  const period = one(params.period) || "week";
  if (!periods.includes(period as (typeof periods)[number])) throw new Error("period должен быть week, month или all");
  const rawHeight = one(params.height) || "480";
  if (!/^\d{2,4}$/.test(rawHeight) || Number(rawHeight) < 240 || Number(rawHeight) > 1000) throw new Error("height должен быть от 240 до 1000 пикселей");
  const filters: EventFilters = { city, category, page: 1, page_size: 12, sort: "date_asc" };
  if (period !== "all") {
    filters.date_from = bashkortostanDate();
    filters.date_to = bashkortostanDate(period === "week" ? 7 : 30);
  }
  return { filters, height: Number(rawHeight) };
}
const detailUrl = (id: string) => `/events/${encodeURIComponent(id)}`;
function price(event: { is_free: boolean; price_min: number | null; price_max: number | null }) {
  if (event.is_free) return "Бесплатно";
  if (event.price_min === null) return null;
  if (event.price_max && event.price_max !== event.price_min) return `${event.price_min.toLocaleString("ru-RU")}–${event.price_max.toLocaleString("ru-RU")} ₽`;
  return `от ${event.price_min.toLocaleString("ru-RU")} ₽`;
}
export default async function Embed({ searchParams }: { searchParams: Promise<Params> }) {
  const params = await searchParams;
  let height = 480;
  let result;
  let message = "";
  try {
    const config = filtersFor(params); height = config.height;
    result = await api.events(config.filters);
  } catch (error) {
    message = error instanceof Error ? error.message : "Не удалось загрузить афишу";
    if (error instanceof ApiError) message = "Афиша временно недоступна. Попробуйте обновить страницу позже.";
  }
  return <div className={styles.widget} style={{ height }}>
    <header className={styles.header}><span className={styles.brand}>Афиша Башкортостана</span><a href="/events" target="_blank" rel="noreferrer">Все события</a></header>
    {message ? <p className={styles.state} role="alert">{message}</p> : result?.events.length ? <div className={styles.list}>
      {result.events.map(event => <article key={event.id} className={styles.event}>
        <p className={styles.date}>{formatDate(event.date_start)}</p><a className={styles.title} href={detailUrl(event.id)} target="_blank" rel="noreferrer">{cardTitle(event.title)}</a>
        <p className={styles.meta}>{event.city} · {event.venue}</p>{price(event) && <p className={styles.price}>{price(event)}</p>}
      </article>)}
    </div> : <p className={styles.state}>Событий пока нет. Попробуйте изменить настройки виджета.</p>}
    <footer className={styles.footer}>Культура Башкортостана</footer>
  </div>;
}
