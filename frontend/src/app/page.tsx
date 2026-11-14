import Link from "next/link";
import { Suspense } from "react";
import { SearchBar, LoadingState } from "@/components/ui";
import { EventPreview } from "@/components/event-preview";
import { api } from "@/lib/api";
import { eventsHref } from "@/lib/event-query";

export const dynamic = "force-dynamic";

async function ExploreLinks() {
  const [cities, categories] = await Promise.allSettled([api.cities(), api.categories()]);
  return <div className="explore-grid">
    <div className="explore-group"><h2>Город</h2><div className="explore-links">
      {cities.status === "fulfilled" && cities.value.slice(0, 8).map(item => <Link key={item.city} href={eventsHref({ city: item.city })}>{item.city}<small>{item.cnt}</small></Link>)}
      <Link href="/events">Все города</Link>
    </div></div>
    <div className="explore-group"><h2>Интересы</h2><div className="explore-links">
      {categories.status === "fulfilled" && categories.value.slice(0, 8).map(item => <Link key={item.category} href={eventsHref({ category: item.category })}>{item.category}<small>{item.cnt}</small></Link>)}
      <Link href="/events">Все события</Link>
    </div></div>
  </div>;
}

export default function Home() {
  const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Yekaterinburg" });
  return <>
    <section className="home-search"><div className="container home-search-inner">
      <div className="home-search-heading"><span className="issue-label">Башкортостан / события</span><h1>Найти событие</h1><p>Выберите, куда пойти, по городу, дате или интересу.</p></div>
      <form action="/events" className="home-search-form"><SearchBar /></form>
      <nav className="date-links" aria-label="Поиск по дате"><span>Когда</span><Link href={eventsHref({ date_from: today, date_to: today })}>Сегодня</Link><Link href={eventsHref({ date_from: today, sort: "date_asc" })}>Ближайшие</Link><Link href="/events">Выбрать даты в каталоге</Link></nav>
    </div></section>
    <section className="container home-explore" aria-label="Быстрый выбор"><Suspense fallback={<LoadingState />}><ExploreLinks /></Suspense></section>
    <section className="container section events-section"><div className="section-heading"><div><span className="issue-label">По дате / от ближайшего</span><h2>Ближайшие события</h2></div><Link className="text-link" href="/events">Открыть каталог</Link></div><Suspense fallback={<LoadingState />}><EventPreview limit={6} filters={{ date_from: today, sort: "date_asc" }} /></Suspense></section>
    <section className="container home-agent"><div><span className="issue-label">Поиск с уточнением</span><h2>Нужен совет?</h2><p>Опишите город, день и бюджет. Помощник подберёт события из афиши.</p></div><Link className="button" href="/agent">Спросить помощника</Link></section>
  </>;
}
