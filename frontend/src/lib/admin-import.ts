export type ImportRejection = {
  row?: number | string;
  id?: string;
  title?: string;
  reason?: string;
};

export type ImportReport = {
  added_count: number;
  updated_count: number;
  rejected_count: number;
  rejected_reasons?: ImportRejection[];
};

const INVALID_UTF8_MESSAGE = "Файл имеет некорректную кодировку. Сохраните его в UTF-8 и повторите загрузку.";

export async function readImportFile(file: Pick<File, "name" | "size" | "arrayBuffer">): Promise<string> {
  const extension = file.name.split(".").pop()?.toLocaleLowerCase("en-US");
  if (extension !== "csv" && extension !== "json") {
    throw new Error("Для импорта выберите файл CSV или JSON.");
  }
  if (file.size === 0) throw new Error("Файл пустой.");

  let content: string;
  try {
    content = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
  } catch {
    throw new Error(INVALID_UTF8_MESSAGE);
  }
  if (!content.trim()) throw new Error("Файл пустой.");
  return content;
}

export async function readImportResponse(response: Pick<Response, "ok" | "status" | "json">): Promise<ImportReport> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    if (!response.ok) throw new Error(`Ошибка импорта на сервере (HTTP ${response.status}).`);
    throw new Error("Сервер вернул некорректный отчёт импорта.");
  }

  if (!response.ok) {
    const payload = body as { detail?: unknown; error?: { message?: unknown } } | null;
    const detail = payload?.error?.message ?? payload?.detail;
    const message = typeof detail === "string" && !detail.includes("Traceback") ? detail : "";
    throw new Error(message || `Ошибка импорта на сервере (HTTP ${response.status}).`);
  }
  return body as ImportReport;
}
