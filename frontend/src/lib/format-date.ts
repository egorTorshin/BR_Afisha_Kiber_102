export function formatDate(value: string) {
  // Naive backend timestamps are local to Bashkortostan (UTC+5).
  const date = new Date(
    /[zZ]|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}+05:00`,
  );
  return Number.isNaN(date.getTime())
    ? "Дата уточняется"
    : new Intl.DateTimeFormat("ru-RU", {
        day: "numeric",
        month: "long",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Asia/Yekaterinburg",
      }).format(date);
}
