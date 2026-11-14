import { redirect } from "next/navigation";
import { eventsHref, parseEventQuery, type SearchParams } from "@/lib/event-query";

export const dynamic = "force-dynamic";

export default async function Events({ searchParams }: { searchParams: Promise<SearchParams> }) {
  redirect(eventsHref(parseEventQuery(await searchParams)));
}
