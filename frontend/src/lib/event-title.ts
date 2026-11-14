export function cardTitle(title: string): string {
  return title.replace(/\s*\(\s*выпуск\s*(?:(?:№|номер|n|#)\s*)?\d+\s*\)\s*$/iu, "").trim();
}
