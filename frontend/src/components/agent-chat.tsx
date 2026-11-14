"use client";

import Link from "next/link";
import { ArrowRight, LoaderCircle, Send } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AgentChatResponse, AgentEventCard } from "@/lib/types";
import { formatDate } from "./ui";
import { VoiceInput } from "./voice-input";

type ChatMessage = { role: "user" | "assistant"; text: string };

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
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [cards, setCards] = useState<AgentEventCard[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        const saved = JSON.parse(localStorage.getItem("afisha-agent-chat") || "null");
        if (saved?.messages && Array.isArray(saved.messages)) setMessages(saved.messages.slice(-10));
        if (typeof saved?.conversationId === "string") setConversationId(saved.conversationId);
      } catch { localStorage.removeItem("afisha-agent-chat"); }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);
  useEffect(() => {
    if (messages.length || conversationId) localStorage.setItem("afisha-agent-chat", JSON.stringify({ messages: messages.slice(-10), conversationId }));
  }, [messages, conversationId]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = message.trim();
    if (!text || pending) return;
    setMessage("");
    setError(undefined);
    setMessages((current) => [...current, { role: "user", text }]);
    setPending(true);
    try {
      const result: AgentChatResponse = await api.agentChat({
        message: text,
        conversation_id: conversationId,
      });
      setConversationId(result.conversation_id);
      setMessages((current) => [...current, { role: "assistant", text: result.message }]);
      setCards(result.event_cards);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Агент временно недоступен.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className={`agent-chat ${compact ? "agent-chat-compact" : ""}`}>
      <div className="agent-chat-intro">
        <div>
          <p className="eyebrow">ПОИСК В ДИАЛОГЕ</p>
          <h2>Спросите, куда пойти</h2>
          <p>Опишите город, дату, настроение или бюджет — агент уточнит детали и покажет только события из афиши.</p>
        </div>
        <span className="agent-chat-note">Данные из сервиса афиши</span>
      </div>
      <div className="agent-chat-messages" aria-live="polite">
        {messages.length === 0 && (
          <p className="agent-chat-empty">Например: «Куда сходить с ребёнком в Уфе в субботу до 1000 рублей?»</p>
        )}
        {messages.map((item, index) => (
          <div key={`${item.role}-${index}`} className={`agent-message agent-message-${item.role}`}>
            {item.text}
          </div>
        ))}
        {pending && <LoaderCircle className="animate-spin agent-chat-loader" size={20} aria-label="Агент думает" />}
      </div>
      {cards.length > 0 && (
        <div className="agent-card-grid">
          {cards.map((card) => (
            <article className="agent-card" key={card.id}>
              <p className="event-meta">{card.category} · {card.age_limit}+</p>
              <h3><Link href={`/events/${encodeURIComponent(card.id)}`}>{card.title}</Link></h3>
              <p>{formatDate(card.date_start)} · {card.city}</p>
              <div className="agent-card-bottom"><strong>{price(card)}</strong><Link className="text-link" href={`/events/${encodeURIComponent(card.id)}`}>Карточка <ArrowRight size={14} /></Link></div>
            </article>
          ))}
        </div>
      )}
      <form className="agent-chat-form" onSubmit={submit}>
        <label className="sr-only" htmlFor="agent-message">Ваш вопрос агенту</label>
        <VoiceInput disabled={pending} onText={(text) => setMessage((current) => current ? `${current} ${text}` : text)} />
        <input id="agent-message" value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Например: бесплатные выставки в Уфе…" maxLength={4000} disabled={pending} />
        <button className="button" type="submit" disabled={pending || !message.trim()}>{pending ? "Ищем…" : "Спросить"} <Send size={16} /></button>
      </form>
      {error && <p className="agent-chat-error" role="alert">{error}</p>}
    </div>
  );
}
