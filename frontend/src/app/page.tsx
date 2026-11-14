import Link from "next/link";
import { Suspense } from "react";
import { LoadingState } from "@/components/ui";
import { KuraiArt } from "@/components/kurai-art";
import { CatalogForm, ResultsToolbar } from "@/components/catalog-form";
import { EventResults } from "@/components/event-results";
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
      <div className="home-search-heading"><h1>Афиша республики Башкортостан</h1><p>Опишите, куда хотите пойти, или выберите город, дату и интерес ниже.</p><Link className="home-catalog-link" href="#catalog">К поиску событий</Link></div>
      <KuraiArt />
      <nav className="date-links" aria-label="Быстрый выбор даты"><span>Когда</span><Link href={`${eventsHref({ date_from: today, date_to: today })}#catalog`}>Сегодня</Link><Link href={`${eventsHref({ date_from: today, sort: "date_asc" })}#catalog`}>Ближайшие</Link><Link href="#catalog">Выбрать даты</Link></nav>
    </div></section>
    <section id="catalog" className="catalog-page" aria-label="Афиша событий"><div className="container catalog-content">
      <div className="catalog-heading"><span className="issue-label">Поиск по афише</span><h2>События</h2></div>
      <Suspense key={JSON.stringify(params)} fallback={<LoadingState />}><CatalogFilters filters={filters} /></Suspense>
      <ResultsToolbar filters={filters} />
      <section id="results" aria-label="Результаты поиска" tabIndex={-1}>
        <Suspense key={JSON.stringify(filters)} fallback={<LoadingState />}><EventResults filters={filters} /></Suspense>
      </section>
    </div></section>
  </>;
}
