import { api } from "@/lib/api";
import type { CultureEvent } from "@/lib/types";
import { EventCarousel } from "./event-carousel";

export async function FeaturedEvents({ from }: { from: string }) {
  let events: CultureEvent[] = [];
  try {
    const result = await api.events({ date_from: from, sort: "date_asc", page_size: 10 });
    events = result.events;
  } catch {
    return null;
  }
  return events.length ? <EventCarousel events={events} /> : null;
}
