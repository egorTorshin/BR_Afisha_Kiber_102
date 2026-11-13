import Link from "next/link";
import { Suspense } from "react";
import { ArrowDown, ArrowUpRight, ArrowRight, Sparkles } from "lucide-react";
import { SearchBar, LoadingState } from "@/components/ui";
import { EventPreview } from "@/components/event-preview";
import { AgentChat } from "@/components/agent-chat";
export const dynamic = "force-dynamic";
import { api } from "@/lib/api";
import { eventsHref } from "@/lib/event-query";

async function QuickFilters() {
  const [cities, categories] = await Promise.allSettled([
    api.cities(),
    api.categories(),
  ]);
  return (
    <>
      <div className="direction-grid">
        {categories.status === "fulfilled" &&
          categories.value.slice(0, 7).map((item, index) => (
            <Link
              key={item.category}
              className={`direction direction-${index % 4}`}
              href={eventsHref({ category: item.category })}
            >
              <div className="direction-top">
                <span className="direction-symbol" aria-hidden="true">
                  {["◒", "≋", "▧", "✳"][index % 4]}
                </span>
                <ArrowUpRight size={23} />
              </div>
              <h3>{item.category}</h3>
              <p>Событий в афише: {item.cnt}</p>
            </Link>
          ))}
        <Link className="direction direction-3" href="/events">
          <div className="direction-top">
            <span className="direction-symbol" aria-hidden="true">
              ✳
            </span>
            <ArrowUpRight size={23} />
          </div>
          <h3>Вся культура</h3>
          <p>Открыть все направления</p>
        </Link>
      </div>
      {cities.status === "fulfilled" && cities.value.length > 0 && (
        <nav className="quick-cities" aria-label="Афиша по городам">
          <span>Рядом с вами</span>
          {cities.value.slice(0, 8).map((item) => (
            <Link key={item.city} href={eventsHref({ city: item.city })}>
              {item.city}
              <span>{item.cnt}</span>
            </Link>
          ))}
        </nav>
      )}
      {(cities.status === "rejected" || categories.status === "rejected") && (
        <p className="results-note" role="status">
          Быстрые фильтры временно недоступны. Перейдите в каталог или
          попробуйте позже.
        </p>
      )}
    </>
  );
}

export default function Home() {
  return (
    <>
      <section className="hero">
        <div className="container hero-grid">
          <div className="hero-copy">
            <p className="eyebrow">
              <span className="small-dot" /> АФИША КУЛЬТУРЫ БАШКОРТОСТАНА
            </p>
            <h1>
              Ваш следующий
              <br />
              выход — <em>в культуру.</em>
            </h1>
            <p className="hero-description">
              От любимого театра до неожиданной выставки.
              <br className="desktop-break" /> Откройте то, что вдохновляет,
              рядом с вами.
            </p>
            <form action="/events">
              <SearchBar />
            </form>
            <div className="hero-bottom">
              <span>Вся республика. Столько впечатлений.</span>
              <a href="#discover" aria-label="Перейти к направлениям">
                <ArrowDown size={20} />
              </a>
            </div>
          </div>
          <div
            className="hero-art"
            role="img"
            aria-label="Геометрическая композиция, вдохновлённая цветком курая и башкирским орнаментом"
          >
            <div className="art-top">
              <span>
                МЕСТО ДЛЯ
                <br />
                НОВЫХ ВПЕЧАТЛЕНИЙ
              </span>
              <span>РБ / 02</span>
            </div>
            <div className="kurai">
              <i />
              <i />
              <i />
              <i />
              <i />
              <i />
              <i />
              <b />
            </div>
            <div className="art-bottom">
              <span>БАШҠОРТОСТАН</span>
              <span>ОТКРЫВАЙТЕ СВОЁ ↗</span>
            </div>
          </div>
        </div>
      </section>
      <section id="discover" className="container section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">ВЫБИРАЙТЕ ПО НАСТРОЕНИЮ</p>
            <h2>Куда пойдём?</h2>
          </div>
          <span className="section-caption">
            У культуры много направлений.
            <br />
            Найдите своё.
          </span>
        </div>
        <Suspense fallback={<LoadingState />}>
          <QuickFilters />
        </Suspense>
      </section>
      <section className="container section events-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">ПЛАНЫ, КОТОРЫХ СТОИТ ЖДАТЬ</p>
            <h2>На культурном горизонте</h2>
          </div>
          <Link className="text-link" href="/events">
            Вся афиша <ArrowRight size={18} />
          </Link>
        </div>
        <Suspense fallback={<LoadingState />}>
          <EventPreview
            filters={{
              date_from: new Date().toLocaleDateString("sv-SE", {
                timeZone: "Asia/Yekaterinburg",
              }),
              sort: "date_asc",
            }}
          />
        </Suspense>
      </section>
      <section className="container section agent-section" id="agent">
        <AgentChat compact />
      </section>
      <section className="container about-strip">
        <Sparkles size={30} aria-hidden="true" />
        <div>
          <h2>Большая культура начинается с любопытства.</h2>
          <p>
            Объединяем события республики, чтобы вам было проще находить своё.
          </p>
        </div>
        <Link href="/feedback" className="text-link">
          Есть идея? Напишите <ArrowUpRight size={18} />
        </Link>
      </section>
    </>
  );
}
