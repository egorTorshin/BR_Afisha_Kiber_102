"use client";

import Link from "next/link";
import { ArrowRight, Send } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { AgentChatResponse, AgentEventCard } from "@/lib/types";
import { cardTitle } from "@/lib/event-title";
import { formatDate } from "@/lib/format-date";
import { VoiceInput } from "./voice-input";

function price(card: AgentEventCard) {
  if (card.is_free) return "Бесплатно";
  if (card.price_min !== null && card.price_max !== null) {
    return card.price_min === card.price_max
      ? `${card.price_min.toLocaleString("ru-RU")} ₽`
      : `${card.price_min.toLocaleString("ru-RU")}–${card.price_max.toLocaleString("ru-RU")} ₽`;
  }
  if (card.price_min !== null) return `от ${card.price_min.toLocaleString("ru-RU")} ₽`;
  if (card.price_max !== null) return `до ${card.price_max.toLocaleString("ru-RU")} ₽`;
  return "Цена уточняется";
}

export function AgentChat({ compact = false }: { compact?: boolean }) {
  const [message, setMessage] = useState("");
  const [conversationId, setConversationId] = useState<string>();
  const [cards, setCards] = useState<AgentEventCard[]>([]);
  const [note, setNote] = useState<string>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        const saved = JSON.parse(sessionStorage.getItem("afisha-agent-search") || "null");
        const restoredCards = saved?.cards && Array.isArray(saved.cards) ? saved.cards.slice(0, 12) : [];
        setCards(restoredCards);
        if (typeof saved?.conversationId === "string") setConversationId(saved.conversationId);
        if (typeof saved?.note === "string" && !restoredCards.length) setNote(saved.note);
      } catch { sessionStorage.removeItem("afisha-agent-search"); }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);
  useEffect(() => {
    if (conversationId || cards.length || note) sessionStorage.setItem("afisha-agent-search", JSON.stringify({ cards, conversationId, note }));
  }, [cards, conversationId, note]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = message.trim();
    if (!text || pending) return;
    setMessage("");
    setError(undefined);
    setNote(undefined);
    setCards([]);
    setPending(true);
    try {
      let result: AgentChatResponse;
      try {
        result = await api.agentChat({ message: text, conversation_id: conversationId });
      } catch (requestError) {
        if (!(requestError instanceof ApiError && requestError.status === 403 &&
          requestError.message === "Диалог недоступен" && conversationId)) throw requestError;
        sessionStorage.removeItem("afisha-agent-search");
        setConversationId(undefined);
        result = await api.agentChat({ message: text });
      }
      setConversationId(result.conversation_id);
      setCards(result.event_cards);
      setNote(result.event_cards.length ? undefined : result.message);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Агент временно недоступен.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className={`agent-chat ${compact ? "agent-chat-compact" : ""}`}>
      <form className="agent-chat-form" onSubmit={submit}>
        <label className="sr-only" htmlFor="agent-message">Опишите нужное событие</label>
        <input id="agent-message" value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Например: бесплатные выставки в Уфе…" maxLength={4000} disabled={pending} />
        <VoiceInput disabled={pending} onText={(text) => setMessage((current) => current ? `${current} ${text}` : text)} />
        <button className="button" type="submit" disabled={pending || !message.trim()}>{pending ? "Ищем…" : "Спросить"} <Send size={16} /></button>
      </form>
      {note && <p className="agent-search-note" role="status">{note}</p>}
      {cards.length > 0 && (
        <div className="agent-search-results" aria-live="polite"><p className="results-note">Подобрано событий: {cards.length}</p><div className="agent-card-grid">
          {cards.map((card) => (
            <Link className="agent-card" href={`/events/${encodeURIComponent(card.id)}`} aria-label={`Открыть событие: ${cardTitle(card.title)}`} key={card.id}>
              <p className="event-meta">{card.category} · {card.age_limit}+</p>
              <h3>{cardTitle(card.title)}</h3>
              <p>{formatDate(card.date_start)} · {card.city}</p>
              <div className="agent-card-bottom"><strong>{price(card)}</strong><span className="text-link">Карточка <ArrowRight size={14} /></span></div>
            </Link>
          ))}
        </div></div>
      )}
      {error && <p className="agent-chat-error" role="alert">{error}</p>}
    </div>
  );
}
