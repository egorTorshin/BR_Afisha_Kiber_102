import type { Metadata } from "next";
import { FeedbackForm } from "@/components/feedback-form";
export const metadata: Metadata = { title: "Обратная связь" };
export default async function Feedback({
  searchParams,
}: {
  searchParams: Promise<{ event_id?: string | string[] }>;
}) {
  const params = await searchParams;
  return (
    <div className="container page-content feedback-layout">
      <div>
        <p className="eyebrow">ДАВАЙТЕ СДЕЛАЕМ АФИШУ ЛУЧШЕ</p>
        <h1 className="page-title">
          Культура
          <br />
          диалога.
        </h1>
        <p className="page-intro">
          Заметили неточность или хотите поделиться идеей? Мы на связи.
        </p>
        <div className="feedback-decoration" aria-hidden="true">
          ↗
        </div>
      </div>
      <FeedbackForm
        eventId={
          typeof params.event_id === "string" ? params.event_id : undefined
        }
      />
    </div>
  );
}
