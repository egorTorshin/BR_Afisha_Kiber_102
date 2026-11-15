import Link from "next/link";
import { Suspense } from "react";
import { LoadingState } from "@/components/ui";
import { KuraiArt } from "@/components/kurai-art";
import { SalavatArt } from "@/components/salavat-art";
import { BashkirOrnament } from "@/components/cultural-art";
import { CatalogForm, ResultsToolbar } from "@/components/catalog-form";
import { EventResults } from "@/components/event-results";
import { FeaturedEvents } from "@/components/featured-events";
import { api } from "@/lib/api";
import { eventsHref, parseEventQuery, type SearchParams } from "@/lib/event-query";
import type { EventFilters } from "@/lib/types";

export const dynamic = "force-dynamic";

async function CatalogFilters({ filters }: { filters: EventFilters }) {
  const [cities, categories, venues] = await Promise.allSettled([
    api.cities(), api.categories(), api.venues(),
  ]);
  return <>
    <CatalogForm
      key={JSON.stringify(filters)}
      filters={filters}
      cities={cities.status === "fulfilled" ? cities.value : []}
      categories={categories.status === "fulfilled" ? categories.value : []}
      venues={venues.status === "fulfilled" ? venues.value : []}
    />
    {[cities, categories, venues].some((item) => item.status === "rejected") &&
      <p className="results-note" role="status">Часть справочников временно недоступна. Фильтры сохранены; повторите загрузку позже.</p>}
  </>;
}

export default async function Home({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Yekaterinburg" });
  const filters = Object.keys(params).length ? parseEventQuery(params) : { ...parseEventQuery(params), date_from: today };
  return <>
    <section className="home-search"><div className="container home-search-inner">
      <div className="home-search-heading">
        <h1>Найдите повод <em>выйти в город.</em></h1>
        <p>Спектакли, концерты и выставки республики — в одной афише. Выберите то, что интересно вам, и составьте свой маршрут.</p>
      </div>
      <div className="hero-poster">
        <SalavatArt className="hero-salavat" />
        <div className="hero-kurai" aria-hidden="true"><KuraiArt /></div>
      </div>
      <nav className="date-links" aria-label="Быстрый выбор даты"><Link href={`${eventsHref({ date_from: today, date_to: today })}#catalog`}>Сегодня <span aria-hidden="true">↗</span></Link><Link href={`${eventsHref({ date_from: today, sort: "date_asc" })}#catalog`}>Ближайшие <span aria-hidden="true">↗</span></Link><Link href="#catalog">Все даты <span aria-hidden="true">↗</span></Link></nav>
    </div></section>
    <Suspense fallback={null}><FeaturedEvents from={today} /></Suspense>
    <section id="catalog" className="catalog-page" aria-label="Афиша событий"><div className="container catalog-content">
      <div className="catalog-heading"><div><span className="issue-label">Выберите своё событие</span><h2>Афиша <em>событий</em></h2><BashkirOrnament className="catalog-ornament" /></div><p>Опишите событие в умном поиске или уточните город, дату и формат — подборка обновится под ваш запрос.</p></div>
      <Suspense fallback={<LoadingState />}><CatalogFilters filters={filters} /></Suspense>
      <ResultsToolbar filters={filters} />
      <section id="results" aria-label="Результаты поиска" tabIndex={-1}>
        <Suspense fallback={<LoadingState />}><EventResults filters={filters} /></Suspense>
      </section>
    </div></section>
  </>;
}
