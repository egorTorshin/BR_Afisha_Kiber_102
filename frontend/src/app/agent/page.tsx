import { AgentChat } from "@/components/agent-chat";

export const dynamic = "force-dynamic";

export default function AgentPage() {
  return (
    <main className="container page agent-page">
      <p className="eyebrow">ПОМОЩНИК АФИШИ</p>
      <h1>Найдём впечатления вместе</h1>
      <p className="page-intro">Агент работает с актуальными опубликованными событиями и честно уточняет запрос, если данных недостаточно.</p>
      <AgentChat />
    </main>
  );
}
