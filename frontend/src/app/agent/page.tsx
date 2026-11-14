import { AgentChat } from "@/components/agent-chat";

export const dynamic = "force-dynamic";

export default function AgentPage() {
  return (
    <div className="container page-content agent-page">
      <p className="issue-label">Каталог / помощь в поиске</p>
      <h1 className="page-title">Поиск с помощником</h1>
      <p className="page-intro">Опишите, что ищете. Можно уточнить город, дату, возраст и бюджет.</p>
      <AgentChat />
    </div>
  );
}
