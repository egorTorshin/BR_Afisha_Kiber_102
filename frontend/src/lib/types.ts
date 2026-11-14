/** Wire contracts: src/api.py and src/models/schemas.py. */
export interface EventSource {
  source_url: string;
  source_type: string;
  verified_at: string;
}
export interface EventScheduleItem {
  date_start: string;
  date_end: string | null;
  source_url: string;
}
export interface CultureEvent {
  id: string;
  title: string;
  category: string;
  tags: string[];
  date_start: string;
  date_end: string | null;
  duration_min: number | null;
  city: string;
  venue: string;
  address: string;
  lat: number | null;
  lon: number | null;
  description: string;
  price_min: number | null;
  price_max: number | null;
  is_free: boolean;
  age_limit: number;
  language: string | null;
  org_link: string | null;
  source_url: string;
  source_type: string;
  verified_at: string;
  confidence: number;
  status: string;
  is_disputed: boolean;
  dispute_reason: string | null;
  sources: EventSource[];
  schedule: EventScheduleItem[];
}
export interface EventPage {
  events: CultureEvent[];
  total: number;
  explanation: string | null;
  page: number;
  page_size: number;
  pages: number;
}
export interface EventFilters {
  query_text?: string;
  city?: string;
  venue?: string;
  category?: string;
  date_from?: string;
  date_to?: string;
  time_after?: string;
  time_before?: string;
  max_duration_min?: number;
  max_price?: number;
  is_free_only?: boolean;
  max_age_limit?: number;
  language?: string;
  tag?: string;
  sort?: "date_asc" | "date_desc" | "price_asc";
  page?: number;
  page_size?: number;
}
export interface CityItem {
  city: string;
  cnt: number;
}
export interface CategoryItem {
  category: string;
  cnt: number;
}
export interface VenueItem {
  city: string;
  name: string;
  address: string;
}
export interface FeedbackInput {
  event_id?: string;
  message: string;
}
export interface FeedbackRecord {
  id: string;
  event_id: string | null;
  message: string;
  status: string;
  created_at: string;
}
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: { field?: string; code?: string; message?: string }[];
  };
  request_id: string;
  detail: string;
}

export interface AgentEventCard {
  id: string;
  title: string;
  category: string;
  date_start: string;
  date_end: string | null;
  city: string;
  venue: string;
  address: string;
  price_min: number | null;
  price_max: number | null;
  is_free: boolean;
  age_limit: number;
  source_url: string;
  org_link: string | null;
}

export interface AgentChatResponse {
  message: string;
  status: "ok" | "clarification" | "confirmation_required" | "no_data" | "fallback" | "error";
  event_cards: AgentEventCard[];
  conversation_id: string;
  request_id: string;
}
