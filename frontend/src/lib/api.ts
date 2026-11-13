import type {
  ApiErrorBody,
  CategoryItem,
  CityItem,
  CultureEvent,
  EventFilters,
  EventPage,
  FeedbackInput,
  FeedbackRecord,
  VenueItem,
  AgentChatResponse,
} from "./types";
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
function query(values: object = {}) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return params.size ? `?${params}` : "";
}
async function request<T>(path: string, init?: RequestInit, timeoutMs = 10000): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const base =
      typeof window === "undefined"
        ? (process.env.API_BASE_URL || "http://127.0.0.1:8000").replace(
            /\/$/,
            "",
          )
        : "/backend";
    const response = await fetch(`${base}${path}`, {
      ...init,
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
    if (!response.ok) {
      const body = (await response
        .json()
        .catch(() => null)) as ApiErrorBody | null;
      throw new ApiError(
        body?.error?.message ||
          "Сервис временно недоступен. Попробуйте ещё раз.",
        response.status,
        body?.request_id,
      );
    }
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      "Не удалось связаться с афишей. Попробуйте ещё раз немного позже.",
      0,
    );
  } finally {
    clearTimeout(timeout);
  }
}
export const api = {
  events: (filters: EventFilters = {}) =>
    request<EventPage>(`/api/v1/events${query(filters)}`),
  event: (id: string) =>
    request<CultureEvent>(`/api/v1/events/${encodeURIComponent(id)}`),
  cities: () => request<CityItem[]>("/api/v1/cities"),
  categories: (city?: string) =>
    request<CategoryItem[]>(`/api/v1/categories${query({ city })}`),
  venues: (city?: string) =>
    request<VenueItem[]>(`/api/v1/venues${query({ city })}`),
  feedback: (input: FeedbackInput) =>
    request<FeedbackRecord>("/feedback", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  agentChat: (input: { message: string; conversation_id?: string }) =>
    request<AgentChatResponse>(
      "/api/v1/agent/chat",
      { method: "POST", body: JSON.stringify(input) },
      120000,
    ),
};
