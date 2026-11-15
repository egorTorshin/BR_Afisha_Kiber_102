# Финальный benchmark качества проекта

Дата UTC: 2026-11-15T04:53:11+00:00
Основной набор: 89 кейсов, SHA256 `28c914601725cbe1d7651f4f598cc4d29d6c68d5a8639134a3a10787c029606f`.
Holdout: 10 кейсов, SHA256 `5d24440895279084b2c19e4805d93f4183238348ef52b69fec878580c8824e8f`.

Все пустые ручные annotation-слоты означают UNVERIFIED, а не нулевую ошибку.

## Метрики 9.1 и парсера

| Метрика | Результат | Порог | Статус |
|---|---:|---:|---|
| catalog_completeness | 100.0 | >=95% | PASS |
| duplicate_rate | 0.0 | <=2% (уровень 1–2); <=5% (уровень 4) | PASS |
| search_recall | — | >=90% | UNVERIFIED |
| false_empty_rate | — | <=5% | UNVERIFIED |
| search_only_p95_seconds | — | <=1.5 | UNVERIFIED |
| import_100_first_pass | — | 100 событий без ручной правки и отказов | UNVERIFIED |
| import_100_repeat | — | 0 добавлений, 100 unchanged, 0 отказов | UNVERIFIED |
| collection_extraction_accuracy_50 | — | >=90% | UNVERIFIED |
| imported_duplicate_rate | — | <=5% | UNVERIFIED |
| cancelled_events_shown_as_active | — | 0 | UNVERIFIED |

## Метрики 9.2 агента

| Метрика | Primary | Holdout | Худшее | Порог L4 | Статус |
|---|---:|---:|---:|---:|---|
| factual_accuracy_pct | — | — | — | 98 | UNVERIFIED |
| hallucinations | 0 | 0 | 0 | 0 | PASS |
| constraint_satisfaction_pct | 100.0 | 100.0 | 100.0 | 90 | PASS |
| clarification_accuracy_pct | 100.0 | 100.0 | 100.0 | 90 | PASS |
| honest_refusal_pct | 50.0 | 100.0 | 50.0 | 100 | FAIL |
| tool_accuracy_pct | 100.0 | 100.0 | 100.0 | 92 | PASS |
| injection_pass_pct | — | — | — | 100 | UNVERIFIED |
| p95_latency_seconds | 14.109923583921045 | 10.714366334024817 | 14.109923583921045 | 6 | FAIL |
| avg_dialogue_cost_rub | — | — | — | 3 | UNVERIFIED |

## Уровень 3 ТЗ

| Метрика | Значение | Порог | Статус |
|---|---:|---:|---|
| factual_accuracy_pct | — | >=95 | UNVERIFIED |
| hallucinations | 0 | 0 | UNVERIFIED |
| constraint_satisfaction_pct | 100.0 | >=80 | PASS |
| clarification_accuracy_pct | 100.0 | >=80 | PASS |
| honest_refusal_pct | 50.0 | >=100 | FAIL |
| tool_accuracy_pct | 100.0 | >=85 | PASS |
| injection_pass_pct | — | >=100 | UNVERIFIED |
| p95_latency_seconds | 14.109923583921045 | <=8 | FAIL |
| avg_dialogue_cost_rub | — | <=5 | UNVERIFIED |
| cancelled_events_shown_as_active | 0 | 0 | UNVERIFIED |
| reliability_pct | 100.0 | 100% of 20 + LLM outage | PASS |

## Отдельный простой поиск (вне зачёта уровня 3)

2/8 технически валидных ответов; полная p50/p95 = 3.6408715000000003/13.008447 с. Нет внешнего holdout и ручной сверки фактов.

## Итог

Коэффициент качества 9.3: **UNVERIFIED** — формула раздела 9.3 и необходимые подтверждённые входные данные не найдены в доступном ТЗ. Не вычисляется по произвольной формуле.

Источники gold для фактических утверждений, search recall и 50 карточек вынесены в `cases.json`; их ручное заполнение и рецензирование обязательно.
