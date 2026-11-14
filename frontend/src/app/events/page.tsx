import type { Metadata } from "next";
import { Suspense } from "react";
import { api } from "@/lib/api";
import { LoadingState } from "@/components/ui";
import { CatalogForm, ResultsToolbar } from "@/components/catalog-form";
import { EventResults } from "@/components/event-results";
import type { EventFilters } from "@/lib/types";
import { parseEventQuery, type SearchParams } from "@/lib/event-query";

export const metadata: Metadata = { title: "Афиша событий" };
export const dynamic = "force-dynamic";

async function CatalogFilters({ filters }: { filters: EventFilters }) {
  const dictionaries = Promise.allSettled([
    api.cities(),
    api.categories(),
    api.venues(),
  ]);
  const [cities, categories, venues] = await dictionaries;
  return (
    <>
      <CatalogForm
        key={JSON.stringify(filters)}
        filters={filters}
        cities={cities.status === "fulfilled" ? cities.value : []}
        categories={categories.status === "fulfilled" ? categories.value : []}
        venues={venues.status === "fulfilled" ? venues.value : []}
      />
      {[cities, categories, venues].some(
        (item) => item.status === "rejected",
      ) && (
        <p className="results-note" role="status">
          Часть справочников временно недоступна. Текущие фильтры сохранены;
          поиск и ввод площадки доступны. Повторите загрузку позже.
        </p>
      )}
    </>
  );
}

export default async function Events({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  const filters = parseEventQuery(params);
  return (
    <div className="container page-content">
      <p className="issue-label">Каталог / Башкортостан</p>
      <h1 className="page-title">События</h1>
      <p className="page-intro">Найдите событие по названию, городу, дате и интересу.</p>
      <Suspense key={JSON.stringify(params)} fallback={<LoadingState />}>
        <CatalogFilters filters={filters} />
      </Suspense>
      <ResultsToolbar filters={filters} />
      <section id="results" aria-label="Результаты поиска" tabIndex={-1}>
        <Suspense key={JSON.stringify(filters)} fallback={<LoadingState />}>
          <EventResults filters={filters} />
        </Suspense>
      </section>
    </div>
  );
}
