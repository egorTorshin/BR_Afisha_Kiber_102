"use client";
import { useState, type FormEvent } from "react";
import { ArrowUpRight, CheckCircle2 } from "lucide-react";
import { api } from "@/lib/api";
export function FeedbackForm({ eventId }: { eventId?: string }) {
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const message = String(
      new FormData(event.currentTarget).get("message") || "",
    ).trim();
    if (!message) {
      setError("Добавьте текст сообщения.");
      return;
    }
    setPending(true);
    setError("");
    try {
      await api.feedback({
        message,
        ...(eventId ? { event_id: eventId } : {}),
      });
      setSent(true);
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "Не удалось отправить сообщение.",
      );
    } finally {
      setPending(false);
    }
  }
  if (sent)
    return (
      <div className="feedback-success" role="status">
        <CheckCircle2 size={32} />
        <h2>Спасибо, что помогаете!</h2>
        <p>Сообщение принято. Так афиша становится точнее.</p>
        <button className="text-link" onClick={() => setSent(false)}>
          Отправить ещё одно сообщение
        </button>
      </div>
    );
  return (
    <form className="feedback-form" onSubmit={submit} aria-busy={pending}>
      {eventId && (
        <p className="results-note">
          Сообщение будет связано с событием: {eventId}
        </p>
      )}
      <label htmlFor="message">
        Ваше сообщение <span aria-hidden="true">*</span>
      </label>
      <p id="message-help">
        Расскажите об идее или неточности. Не указывайте личные данные.
      </p>
      <textarea
        id="message"
        name="message"
        required
        maxLength={10000}
        rows={7}
        aria-describedby={`message-help${error ? " feedback-error" : ""}`}
        placeholder="Что мы можем сделать лучше?"
        disabled={pending}
      />
      {error && (
        <p className="form-error" id="feedback-error" role="alert">
          {error}
        </p>
      )}
      <button className="button" disabled={pending} type="submit">
        {pending ? "Отправляем…" : "Отправить сообщение"}
        <ArrowUpRight size={18} />
      </button>
    </form>
  );
}
