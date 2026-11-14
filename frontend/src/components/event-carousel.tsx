"use client";

import Link from "next/link";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useRef } from "react";
import type { CultureEvent } from "@/lib/types";
import { EventCard } from "./ui";

export function EventCarousel({ events }: { events: CultureEvent[] }) {
  const track = useRef<HTMLDivElement>(null);
  const paused = useRef(false);
  const manualPauseUntil = useRef(0);
  const copies = events.length < 4 ? 3 : 2;

  useEffect(() => {
    const node = track.current;
    if (!node || events.length < 2 || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let span = 0;
    let step = 0;
    const measure = () => {
      const items = node.querySelectorAll<HTMLElement>(".carousel-item");
      span = items[events.length]?.offsetLeft - items[0]?.offsetLeft || 0;
      step = items[1]?.offsetLeft - items[0]?.offsetLeft || 0;
    };
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    measure();
    const timer = window.setInterval(() => {
      if (!step || paused.current || performance.now() < manualPauseUntil.current || document.hidden) return;
      node.scrollBy({ left: step, behavior: "smooth" });
      window.setTimeout(() => { if (span && node.scrollLeft >= span - 2) node.scrollLeft -= span; }, 900);
    }, 4400);
    return () => { window.clearInterval(timer); observer.disconnect(); };
  }, [events.length]);

  function move(direction: -1 | 1) {
    const node = track.current;
    if (!node || events.length < 2) return;
    const items = node.querySelectorAll<HTMLElement>(".carousel-item");
    const span = items[events.length]?.offsetLeft - items[0]?.offsetLeft || 0;
    const step = items[1]?.offsetLeft - items[0]?.offsetLeft || node.clientWidth;
    if (direction < 0 && node.scrollLeft < step && span) node.scrollLeft += span;
    manualPauseUntil.current = performance.now() + 1800;
    node.scrollBy({ left: direction * step, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }

  return <section className="recommendations container" aria-labelledby="recommendations-heading">
    <div className="recommendations-head">
      <h2 id="recommendations-heading">Может заинтересовать</h2>
      <Link className="recommendations-all" href="/#catalog">Все события <span aria-hidden="true">↗</span></Link>
    </div>
    <div ref={track} className="carousel-track" role="region" aria-label="Рекомендованные события" tabIndex={0}
      onPointerEnter={() => { paused.current = true; }} onPointerLeave={() => { paused.current = false; }}
      onFocus={() => { paused.current = true; }} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) paused.current = false; }}>
      {Array.from({ length: copies }, (_, copy) => events.map(event =>
        <div className="carousel-item" key={`${copy}-${event.id}`}>
          <EventCard event={event} returnHref="/#catalog" />
        </div>))}
    </div>
    <div className="recommendations-actions" aria-label="Управление лентой">
      <button type="button" onClick={() => move(-1)} disabled={events.length < 2} aria-label="Предыдущие события"><ChevronLeft size={20} /></button>
      <button type="button" onClick={() => move(1)} disabled={events.length < 2} aria-label="Следующие события"><ChevronRight size={20} /></button>
    </div>
  </section>;
}
