import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, ArrowUpRight, CalendarDays, MapPin } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { EventStatusBadge, ErrorState } from "@/components/ui";
import { formatDate } from "@/lib/format-date";
import { ShareEvent } from "@/components/share-event";
import { RecordView } from "@/components/record-view";
import { EventMap } from "@/components/event-map";
import { BashkirOrnament } from "@/components/cultural-art";

export const dynamic = "force-dynamic";
type Props = { params: Promise<{ id: string }>; searchParams: Promise<{ returnTo?: string | string[] }> };

async function getEvent(id: string) {
  return api.event(id);
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  try {
    const event = await getEvent(id);
    const description = event.description?.trim().slice(0, 155) ||
      `${event.category || "Культурное событие"} в ${event.city || "Башкортостане"}. Информация из источника организатора.`;
    return {
      title: event.title || "Мероприятие",
      description,
      alternates: { canonical: `/events/${encodeURIComponent(event.id)}` },
      openGraph: { title: event.title, description, type: "article", url: `/events/${encodeURIComponent(event.id)}` },
    };
  } catch {
    return { title: "Мероприятие не найдено" };
  }
}

function safeUrl(value: string | null | undefined) {
  try {
    const url = new URL(value || "");
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}

export default async function EventDetail({ params, searchParams }: Props) {
  const [{ id }, query] = await Promise.all([params, searchParams]);
  let event;
  try {
    event = await getEvent(id);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    return <div className="container page-content"><ErrorState message={error instanceof Error ? error.message : undefined} /></div>;
  }

  const requestedReturn = Array.isArray(query.returnTo) ? query.returnTo[0] : query.returnTo;
  const returnHref = requestedReturn && (/^\/\?(?!\/)/.test(requestedReturn) || requestedReturn.startsWith("/events?")) ? requestedReturn : "/#catalog";
  const organizer = safeUrl(event.org_link);
  const cancelled = event.status === "cancelled" || event.status === "отменено";
  const time = formatDate(event.date_start);
  const start = new Date(/[zZ]|[+-]\d{2}:\d{2}$/.test(event.date_start) ? event.date_start : `${event.date_start}+05:00`);
  const end = event.date_end ? new Date(/[zZ]|[+-]\d{2}:\d{2}$/.test(event.date_end) ? event.date_end : `${event.date_end}+05:00`) : null;
  const timeRange = end && !Number.isNaN(end.getTime()) && !Number.isNaN(start.getTime()) && start.toDateString() === end.toDateString()
    ? `${time}–${new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Yekaterinburg" }).format(end)}` : time;
  const price = event.is_free ? "Вход свободный" : event.price_min !== null
    ? event.price_max !== null && event.price_max !== event.price_min
      ? `${event.price_min.toLocaleString("ru-RU")}–${event.price_max.toLocaleString("ru-RU")} ₽`
      : `от ${event.price_min.toLocaleString("ru-RU")} ₽`
    : "Стоимость не указана";
  const description = event.description?.trim() || "";
  const descriptionCutOff = description.length < 180 && /[,;:—–-]$/.test(description);

  return (
    <article className="container page-content detail" itemScope itemType="https://schema.org/Event">
      <RecordView id={event.id} />
      <nav className="detail-navigation" aria-label="Навигация по афише">
        <Link className="text-link" href={returnHref}><ArrowLeft size={18} aria-hidden="true" /> К афише</Link>
        <ShareEvent />
      </nav>
      {cancelled && <aside className="cancelled-notice" role="alert"><strong>Мероприятие отменено</strong><span>Перед посещением проверьте информацию у организатора.</span></aside>}
      <div className="detail-tags">
        <span>{event.category || "Категория не указана"}</span>
        <EventStatusBadge status={event.status} disputed={event.is_disputed} />
      </div>
      <h1 className="page-title" itemProp="name">{event.title || "Мероприятие"}</h1>
      <BashkirOrnament className="detail-ornament" />
      <dl className="detail-facts">
        <div><dt>Дата и время</dt><dd><CalendarDays size={19} aria-hidden="true" /><time itemProp="startDate" dateTime={event.date_start}>{timeRange}</time></dd></div>
        <div><dt>Площадка и адрес</dt><dd><MapPin size={19} aria-hidden="true" /><span><span itemProp="location" itemScope itemType="https://schema.org/Place"><span itemProp="name">{event.venue || "Площадка не указана"}</span></span>{event.city ? ` · ${event.city}` : ""}{event.address ? <><br /><span itemProp="streetAddress">{event.address}</span></> : null}</span></dd></div>
        <div><dt>Стоимость</dt><dd>{price}</dd></div>
        <div><dt>Возрастное ограничение</dt><dd>{Number.isFinite(event.age_limit) ? `${event.age_limit}+` : "Не указано"}</dd></div>
        <div><dt>Язык мероприятия</dt><dd>{event.language || "Не указан"}</dd></div>
      </dl>
      {event.schedule?.length > 1 && <section className="event-schedule" aria-labelledby="event-schedule-heading"><div className="event-schedule-heading"><span className="issue-label">Выберите удобное время</span><h2 id="event-schedule-heading">Расписание показов <small>{event.schedule.length}</small></h2></div><div className="event-schedule-list">{event.schedule.map((show, index) => <div className="event-schedule-row" key={`${show.date_start}-${index}`}><time dateTime={show.date_start}>{formatDate(show.date_start)}</time>{safeUrl(show.source_url) && <a href={safeUrl(show.source_url)} target="_blank" rel="noopener noreferrer">На сайт площадки <ArrowUpRight size={15} aria-hidden="true" /></a>}</div>)}</div></section>}
      {safeUrl(event.source_url) && <a className="detail-source text-link" href={safeUrl(event.source_url)} target="_blank" rel="noopener noreferrer">Открыть источник события <ArrowUpRight size={16} aria-hidden="true" /><span className="sr-only"> (откроется в новой вкладке)</span></a>}
      <EventMap lat={event.lat} lon={event.lon} title={event.venue || event.title} />
      <section className="detail-copy" aria-labelledby="event-description-heading">
        <h2 id="event-description-heading">О событии</h2>
        <p itemProp="description">{description && !descriptionCutOff ? description : "Подробности смотрите у организатора."}</p>
      </section>
      {event.tags?.length > 0 && <section className="detail-section" aria-labelledby="event-tags-heading"><h2 id="event-tags-heading">Теги</h2><ul className="event-tags">{event.tags.map((tag, index) => <li key={`${tag}-${index}`}>{tag}</li>)}</ul></section>}
      {organizer && <a className="button organizer-link" href={organizer} target="_blank" rel="noopener noreferrer" itemProp="url">Организатор <ArrowUpRight size={17} aria-hidden="true" /><span className="sr-only"> (откроется в новой вкладке)</span></a>}
      <Link className="text-link detail-feedback" href={`/feedback?event_id=${encodeURIComponent(event.id)}`}>Сообщить о неточности <ArrowUpRight size={16} aria-hidden="true" /></Link>
    </article>
  );
}
