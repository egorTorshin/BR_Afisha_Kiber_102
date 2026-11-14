import type { Metadata } from "next";
import { EventSubmissionForm } from "@/components/event-submission-form";

export const metadata: Metadata = { title: "Добавить событие" };

export default function SubmitEventPage() {
  return <main className="container page-content submission-page">
    <p className="eyebrow">Предложить событие</p>
    <h1 className="page-title">Добавить событие</h1>
    <p className="page-intro">Заполните сведения и укажите страницу организатора. Редактор проверит заявку перед публикацией в афише.</p>
    <EventSubmissionForm />
  </main>;
}
