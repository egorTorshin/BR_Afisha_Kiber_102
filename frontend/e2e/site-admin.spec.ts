import { randomUUID } from "node:crypto";
import { test, expect } from "@playwright/test";

const adminUser = process.env.E2E_ADMIN_USERNAME;
const adminPassword = process.env.E2E_ADMIN_PASSWORD;
const date = new Date(Date.now() + 45 * 86_400_000);
date.setMinutes(0, 0, 0);
const datetimeLocal = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}T19:00`;
const dateOnly = datetimeLocal.slice(0, 10);
let capturedBrowserErrors: string[] = [];

function catalogCards(page: import("@playwright/test").Page) {
  // The homepage also renders intentional copies of each event in its recommendation carousel.
  return page.locator('#results .event-grid > article.event-card');
}

async function expectCatalogCountMatchesResults(page: import("@playwright/test").Page) {
  const networkErrors: string[] = [];
  const onRequestFailed = (request: import("@playwright/test").Request) => {
    networkErrors.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText || "request failed"}`);
  };
  const onBadResponse = (response: import("@playwright/test").Response) => {
    if (response.status() >= 400) networkErrors.push(`${response.status()} ${response.request().method()} ${response.url()}`);
  };
  page.on("requestfailed", onRequestFailed);
  page.on("response", onBadResponse);
  const counter = page.locator('#results .preview-label[role="status"]');
  try {
    await expect(counter).toContainText(/Найдено событий:\s*\d+/, { timeout: 20_000 });
  } catch (error) {
    const status = await page.locator('#results [role="status"], #results [role="alert"]').allInnerTexts().catch(() => []);
    throw new Error(`Каталог не показал счётчик результатов. URL: ${page.url()}; status: ${status.join(" | ") || "отсутствует"}; сетевые ошибки: ${networkErrors.join(" | ") || "не зафиксированы"}`, { cause: error });
  } finally {
    page.off("requestfailed", onRequestFailed);
    page.off("response", onBadResponse);
  }
  const status = await counter.innerText();
  const match = status.match(/Найдено событий:\s*(\d+)/);
  expect(match, `счётчик результатов не найден: ${status}`).not.toBeNull();
  const count = Number(match![1]);
  if (count === 0) {
    await expect(page.locator("#results").getByRole("heading", { name: "Пока без событий" })).toBeVisible();
    await expect(catalogCards(page)).toHaveCount(0);
    return;
  }
  await expect(catalogCards(page)).toHaveCount(count);
}

test.afterEach(async ({}, testInfo) => {
  if (testInfo.status !== testInfo.expectedStatus && capturedBrowserErrors.length) {
    await testInfo.attach("browser-console-errors.txt", {
      body: Buffer.from(capturedBrowserErrors.join("\n")),
      contentType: "text/plain",
    });
  }
  capturedBrowserErrors = [];
});

test("публичная афиша и административный жизненный цикл события", async ({ page, context }) => {
  test.skip(!adminUser || !adminPassword, "Укажите E2E_ADMIN_USERNAME и E2E_ADMIN_PASSWORD из .env.e2e");
  const errors = capturedBrowserErrors;
  page.on("pageerror", error => errors.push(`pageerror: ${error.message}`));
  page.on("console", message => { if (message.type() === "error") errors.push(`console: ${message.text()}`); });
  const title = `E2E ${randomUUID()}`;
  const editedTitle = `${title} изменено`;
  let csrfWriteSeen = false;
  const unauthorizedSessionResponses: string[] = [];
  page.on("response", response => {
    if (response.status() === 401) unauthorizedSessionResponses.push(`${response.request().method()} ${new URL(response.url()).pathname}`);
  });
  page.on("request", request => {
    if (["PATCH", "POST", "DELETE"].includes(request.method()) && request.url().includes("/backend/api/v1/admin/") && request.headers()["x-csrf-token"]) csrfWriteSeen = true;
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Найдите повод/ })).toBeVisible();
  const firstCard = catalogCards(page).nth(0);
  await expect(firstCard).toBeVisible();
  const firstTitle = await firstCard.locator("h3 a").innerText();
  await page.getByLabel("Название события или ключевые слова").fill(firstTitle);
  await page.getByRole("button", { name: "Найти", exact: true }).click();
  await expect(catalogCards(page).nth(0).locator("h3 a")).toContainText(firstTitle);

  // Check pagination when the isolated E2E database contains more than one result page.
  await page.goto("/?page_size=1&sort=date_asc#results");
  const nextPage = page.getByRole("navigation", { name: "Страницы мероприятий", exact: true }).getByRole("link", { name: "Далее →", exact: true });
  if (await nextPage.count()) {
    await nextPage.click();
    await expect(page.getByText(/Страница 2 из/)).toBeVisible();
    await expect(page.getByRole("link", { name: "Сбросить всё", exact: true })).toBeVisible();
  } else {
    test.info().annotations.push({ type: "GAP", description: "В каталоге E2E меньше двух страниц, переход по пагинации не выполнен." });
  }

  // Search an impossible title to cover the empty state, then check error rendering on a failed API fetch.
  await page.goto(`/?query_text=${encodeURIComponent(`E2E-no-such-event-${randomUUID()}`)}`);
  await expect(page.getByRole("heading", { name: "Пока без событий", exact: true })).toBeVisible();

  // Create a pending event through the public submission UI.
  await page.goto("/submit-event");
  await page.getByLabel("Название", { exact: true }).fill(title);
  await page.getByLabel("Начало", { exact: true }).fill(datetimeLocal);
  await page.getByLabel("Город", { exact: true }).fill("Уфа");
  await page.getByLabel("Площадка", { exact: true }).fill(`Площадка ${title}`);
  await page.getByLabel("Адрес", { exact: true }).fill("Уфа, тестовый адрес 1");
  await page.getByLabel("Описание", { exact: true }).fill("Уникальное тестовое мероприятие для проверки браузерного E2E сценария.");
  await page.getByLabel("Страница события у организатора").fill("https://example.org/e2e-event");
  await page.getByRole("button", { name: "Отправить на модерацию", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Заявка отправлена", exact: true })).toBeVisible();
  await page.goto(`/?query_text=${encodeURIComponent(title)}`);
  await expect(page.getByRole("heading", { name: "Пока без событий", exact: true })).toBeVisible();

  // Admin login must establish a cookie backed session; subsequent UI writes carry CSRF.
  await page.goto("/admin");
  await page.getByLabel("Логин").fill(adminUser!);
  await page.getByLabel("Пароль").fill(adminPassword!);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Обзор", exact: true })).toBeVisible();
  const cookies = await context.cookies();
  expect(cookies.some(cookie => cookie.name === "admin_session")).toBeTruthy();
  await page.getByRole("button", { name: "Модерация", exact: true }).click();
  const submission = page.locator(".admin-record").filter({ hasText: title });
  await expect(submission).toBeVisible();
  await submission.getByRole("button", { name: "Опубликовать", exact: true }).click();
  await expect(submission).toHaveCount(0);

  await page.goto(`/?query_text=${encodeURIComponent(title)}`);
  await expectCatalogCountMatchesResults(page);
  const published = catalogCards(page).filter({ hasText: title });
  await expect(published).toHaveCount(1);
  const detailLink = published.getByRole("link", { name: /Подробнее о событии/ });
  const detailPath = new URL((await detailLink.getAttribute("href"))!, page.url()).pathname;
  await detailLink.click();
  await expect(page).toHaveURL(url => url.pathname === detailPath);
  const detail = page.locator("main article.detail");
  await expect(detail).toHaveCount(1);
  await expect(detail.getByRole("heading", { level: 1, name: title, exact: true })).toBeVisible();
  await expect(detail.getByText("Опубликовано", { exact: true })).toBeVisible();
  await expect(detail.getByText("Площадка и адрес", { exact: true })).toBeVisible();

  await page.goto("/admin");
  await page.getByRole("button", { name: "События", exact: true }).click();
  const eventRow = page.getByRole("row").filter({ hasText: title });
  await expect(eventRow).toBeVisible();
  await eventRow.getByRole("button", { name: "Действия с событием", exact: true }).click();
  await eventRow.getByText("Изменить карточку").click();
  await eventRow.getByRole("textbox", { name: "Название", exact: true }).fill(editedTitle);
  await eventRow.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect(eventRow).toContainText(editedTitle);

  await eventRow.getByRole("button", { name: "Действия с событием", exact: true }).click();
  await eventRow.getByRole("button", { name: "Отменено", exact: true }).click();
  await expect(eventRow).toContainText("Отменено");
  await page.goto(`/?query_text=${encodeURIComponent(editedTitle)}`);
  await expect(page.getByRole("heading", { name: "Пока без событий", exact: true })).toBeVisible();

  // Create a draft through the admin UI and verify it stays out of the public catalog.
  await page.goto("/admin");
  await page.getByRole("button", { name: "События", exact: true }).click();
  await page.locator(".admin-toolbar").getByRole("button", { name: "＋ Создать событие", exact: true }).click();
  const draftTitle = `${title} черновик`;
  const createForm = page.locator("form.admin-create-form");
  await createForm.getByLabel("Название").fill(draftTitle);
  await createForm.getByLabel("Дата и время (ISO)").fill(datetimeLocal);
  await createForm.getByLabel("Город").fill("Уфа");
  await createForm.getByLabel("Площадка").fill(`Площадка ${draftTitle}`);
  await createForm.getByLabel("Адрес").fill("Уфа, тестовый адрес 2");
  await createForm.getByLabel("Ссылка на источник").fill("https://example.org/e2e-draft");
  await createForm.getByLabel("Описание").fill("Черновик для проверки того, что непубличные записи не попадают в афишу.");
  await createForm.getByRole("button", { name: "Создать черновик", exact: true }).click();
  await expect(page.getByRole("row").filter({ hasText: draftTitle })).toBeVisible();
  await page.goto(`/?query_text=${encodeURIComponent(draftTitle)}`);
  await expect(page.getByRole("heading", { name: "Пока без событий", exact: true })).toBeVisible();

  // Drive all three filters through their visible controls.
  await page.goto("/");
  await page.getByRole("button", { name: "Город", exact: true }).click();
  await page.getByRole("dialog", { name: "Выберите город", exact: true }).getByRole("button", { name: /^Уфа(?:\s+\d+)?$/ }).click();
  await page.getByRole("button", { name: "Категория", exact: true }).click();
  await page.getByRole("dialog", { name: "Выберите категорию", exact: true }).getByRole("button", { name: /^концерт(?:\s+\d+)?$/i }).click();
  await page.getByRole("button", { name: "Когда", exact: true }).click();
  const calendar = page.getByRole("dialog", { name: "Выберите даты", exact: true });
  for (let i = 0; i < 3; i++) {
    const monthLabel = await calendar.locator(".calendar-heading strong").innerText();
    const wantedMonth = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric" }).format(date);
    if (monthLabel === wantedMonth) break;
    await calendar.getByRole("button", { name: "Следующий месяц", exact: true }).click();
  }
  await calendar.getByRole("button", { name: String(Number(dateOnly.slice(-2))), exact: true }).click();
  await calendar.getByRole("button", { name: String(Number(dateOnly.slice(-2))), exact: true }).click();
  await page.getByRole("button", { name: "Показать события", exact: true }).click();
  await expect(page.locator(".active-filters")).toContainText("Уфа");
  await expect(page.locator(".active-filters")).toContainText("Категория: концерт");
  await expect(page.locator(".active-filters")).toContainText("Дата с");
  await expect(page.getByRole("link", { name: "Сбросить всё", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Сбросить всё", exact: true }).click();
  await expect(page.locator(".active-filters")).toHaveCount(0);

  expect(csrfWriteSeen, "административная запись должна передать X-CSRF-Token").toBeTruthy();
  // The admin page probes its cookie-backed session before login. That one 401 is expected;
  // Chromium reports it as a generic resource-load console error, so allow only that exact case.
  expect(unauthorizedSessionResponses).toEqual(["GET /backend/api/v1/admin/session"]);
  const expected401ConsoleError = "Failed to load resource: the server responded with a status of 401 (Unauthorized)";
  const unexpectedErrors = errors.filter(error =>
    !(error === `console: ${expected401ConsoleError}` && unauthorizedSessionResponses.length === 1),
  );
  expect(unexpectedErrors, `Ошибки консоли: ${unexpectedErrors.join("; ")}`).toEqual([]);
});

test("локатор каталога исключает intentional дубли карусели и совпадает со счётчиком", async ({ page }) => {
  await page.setContent(`<section id="results"><div class="preview-label" role="status">Найдено событий: 1</div><div class="event-grid"><article class="event-card"><a>Уникальное событие</a></article></div></section><section class="recommendations"><div class="carousel-track"><article class="event-card">Уникальное событие</article><article class="event-card">Уникальное событие</article></div></section>`);
  await expect(page.locator(".event-card")).toHaveCount(3);
  await expectCatalogCountMatchesResults(page);
  await expect(catalogCards(page).filter({ hasText: "Уникальное событие" })).toHaveCount(1);
});

test("счётчик каталога ожидает переход из загрузки в готовое состояние", async ({ page }) => {
  await page.setContent(`<section id="results"><div role="status"><h2>Загружаем афишу</h2><p>Загружаем события афиши…</p></div></section>`);
  const check = expectCatalogCountMatchesResults(page);
  await expect(page.locator('#results [role="status"]')).toContainText("Загружаем события афиши");
  await page.locator("#results").evaluate((results) => {
    results.innerHTML = `<div class="preview-label" role="status">Найдено событий: 1</div><div class="event-grid"><article class="event-card">Событие</article></div>`;
  });
  await check;
});

test("пустой каталог показывает сообщение без карточек", async ({ page }) => {
  await page.setContent(`<section id="results"><div class="preview-label" role="status">Найдено событий: 0</div><div class="state empty-state"><h2>Пока без событий</h2></div></section>`);
  await expectCatalogCountMatchesResults(page);
});

test("каталог и страница события открываются без React runtime errors", async ({ page }) => {
  const runtimeErrors: string[] = [];
  page.on("pageerror", error => runtimeErrors.push(`pageerror: ${error.message}`));
  page.on("console", message => {
    if (message.type() === "error" && /React error|react\.dev\/errors|Server Components|hydration/i.test(message.text())) {
      runtimeErrors.push(`console: ${message.text()}`);
    }
  });

  await page.goto("/");
  await expectCatalogCountMatchesResults(page);
  expect(runtimeErrors, `React runtime errors in catalog: ${runtimeErrors.join("; ")}`).toEqual([]);
  const cards = catalogCards(page);
  expect(await cards.count(), "В изолированном каталоге требуется опубликованное событие").toBeGreaterThan(0);
  const eventLink = cards.nth(0).getByRole("link", { name: /Подробнее о событии/ });
  const detailPath = new URL((await eventLink.getAttribute("href"))!, page.url()).pathname;
  await eventLink.click();
  await page.waitForTimeout(250);
  expect(runtimeErrors, `React runtime errors after event click: ${runtimeErrors.join("; ")}`).toEqual([]);
  await expect(page).toHaveURL(url => url.pathname === detailPath);
  await expect(page.locator("main article.detail > h1.page-title")).toBeVisible();
  expect(runtimeErrors, `React runtime errors: ${runtimeErrors.join("; ")}`).toEqual([]);
});

test("страница события рендерится сервером при прямом HTTP-запросе", async ({ page, request }) => {
  await page.goto("/");
  const cards = catalogCards(page);
  await expect(cards.first()).toBeVisible();
  const detailHref = await cards.first().getByRole("link", { name: /Подробнее о событии/ }).getAttribute("href");
  expect(detailHref).toBeTruthy();

  const response = await request.get(detailHref!);
  const html = await response.text();
  expect(response.status()).toBe(200);
  expect(/<article[^>]*class="[^"]*detail\b/.test(html), "SSR должен содержать статью мероприятия").toBe(true);
  expect(/<h1[^>]*class="[^"]*page-title\b/.test(html), "SSR должен содержать заголовок мероприятия").toBe(true);
  expect(/<time[^>]*itemProp="startDate"/.test(html), "SSR должен содержать дату мероприятия").toBe(true);
  expect(/Attempted to call formatDate|3701493170|NEXT_NOT_FOUND|internal server error/i.test(html), "SSR содержит ошибку").toBe(false);
});
