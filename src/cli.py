import argparse
import json
import sys
import time
from pathlib import Path
from src.bot.formatter import format_event_card, format_import_report, format_search_results
from src.config import load_settings
from src.db.connection import Database
from src.db.migrations import apply_migrations, seed_dictionaries
from src.services.import_service import ImportService
from src.services.normalizer import parse_free_text_query
from src.services.search_service import SearchService


def init_db_and_services():
    settings = load_settings()
    db = Database(settings.database_url)
    apply_migrations(db, settings.migrations_dir)
    seed_dictionaries(db, settings.data_dir / "dictionaries.json")
    return settings, db, ImportService(db), SearchService(db)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CLI-инструмент управления каталогом и модерации «Афиша культуры Башкортостана» (Уровень 1)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # 1. import
    p_imp = sub.add_parser("import", help="Импорт событий из файла CSV или JSON")
    p_imp.add_argument("file", type=str, help="Путь к файлу .csv или .json")
    p_imp.add_argument("--actor", type=str, default="cli_moderator", help="Имя инициатора в audit_log")

    # 2. add-manual
    p_man = sub.add_parser("add-manual", help="Ручной ввод нового события через командный скрипт")
    p_man.add_argument("--title", required=True, help="Название мероприятия")
    p_man.add_argument("--category", required=True, help="Категория (концерт, спектакль, выставка, фестиваль, кино, экскурсия, другое)")
    p_man.add_argument("--date-start", required=True, help="Дата и время начала (YYYY-MM-DDTHH:MM:SS или DD.MM.YYYY HH:MM)")
    p_man.add_argument("--city", required=True, help="Город или населённый пункт")
    p_man.add_argument("--venue", required=True, help="Площадка проведения")
    p_man.add_argument("--address", required=True, help="Улица и дом")
    p_man.add_argument("--description", required=True, help="Описание события")
    p_man.add_argument("--source-url", required=True, help="Ссылка на источник")
    p_man.add_argument("--price-min", type=int, default=None, help="Минимальная цена (0 = бесплатно, без параметра — неизвестна)")
    p_man.add_argument("--price-max", type=int, default=None, help="Максимальная цена")
    p_man.add_argument("--age-limit", type=int, default=0, help="Возрастное ограничение (0, 6, 12, 16, 18)")
    p_man.add_argument("--org-link", type=str, default="", help="Ссылка на покупку билетов")
    p_man.add_argument("--status", type=str, default="опубликовано", help="Статус (опубликовано, черновик, отменено)")
    p_man.add_argument("--actor", type=str, default="cli_moderator", help="Кто вносит запись")

    # 3. set-status
    p_st = sub.add_parser("set-status", help="Изменение статуса события (опубликовано, черновик, отменено)")
    p_st.add_argument("event_id", type=str, help="UUID события")
    p_st.add_argument("status", type=str, help="Новый статус: опубликовано | черновик | отменено")
    p_st.add_argument("--actor", type=str, default="cli_moderator")

    # 4. list-disputed
    sub.add_parser("list-disputed", help="Показать очередь спорных карточек на модерации")

    # 5. reprocess-raw
    sub.add_parser("reprocess-raw", help="Переиграть нормализацию всех сырых записей без повторного сбора")

    # 6. audit-log
    p_aud = sub.add_parser("audit-log", help="Просмотр журнала изменений (кто, когда и что изменил)")
    p_aud.add_argument("--limit", type=int, default=20)

    # 7. search
    p_srch = sub.add_parser("search", help="Поиск событий по текстовому запросу")
    p_srch.add_argument("query", type=str, help="Поисковый запрос на русском языке")

    # 8. metrics
    sub.add_parser("metrics", help="Расчёт и проверка всех метрик базового качества (Раздел 9.1 ТЗ)")

    args = parser.parse_args()
    settings, db, imp_service, search_service = init_db_and_services()

    if args.command == "import":
        rep = imp_service.import_from_file(Path(args.file), actor=args.actor)
        print(format_import_report(rep))
        return 0

    if args.command == "add-manual":
        outcome, ev = imp_service.create_event_manual(
            {
                "title": args.title,
                "category": args.category,
                "date_start": args.date_start,
                "city": args.city,
                "venue": args.venue,
                "address": args.address,
                "description": args.description,
                "source_url": args.source_url,
                "price_min": args.price_min,
                "price_max": args.price_max,
                "age_limit": args.age_limit,
                "org_link": args.org_link,
                "status": args.status,
            },
            actor=args.actor,
        )
        print(f"Результат: {outcome}\n")
        print(format_event_card(ev))
        return 0

    if args.command == "set-status":
        ev = imp_service.update_event_status(args.event_id, args.status, actor=args.actor)
        print(f"Статус события {ev.id} успешно изменён на '{ev.status}'.")
        return 0

    if args.command == "list-disputed":
        rows = db.fetchall("SELECT id, title, city, status, confidence, dispute_reason FROM events WHERE is_disputed = 1 AND merged_into IS NULL")
        if not rows:
            print("Спорных карточек нет.")
            return 0
        for r in rows:
            print(f"[{r['id']}] {r['title']} ({r['city']}, статус={r['status']}, conf={r['confidence']}) -> {r['dispute_reason']}")
        return 0

    if args.command == "reprocess-raw":
        cnt = imp_service.reprocess_raw_events(actor="cli_reprocessor")
        print(f"Переиграна нормализация для {cnt} сырых записей из таблицы raw_events.")
        return 0

    if args.command == "audit-log":
        rows = db.fetchall("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?", (args.limit,))
        for r in rows:
            print(f"{r['created_at']} | actor={r['actor']} | action={r['action']} | {r['entity_type']}:{r['entity_id']} | {r['changes_json']}")
        return 0

    if args.command == "search":
        filters = parse_free_text_query(args.query, ref_date=settings.get_reference_date(), db=db)
        events, total, explanation = search_service.search(filters)
        print(format_search_results(events, total, filters, empty_explanation=explanation))
        return 0

    if args.command == "metrics":
        return run_quality_metrics(settings, db, imp_service, search_service)

    return 0


def run_quality_metrics(settings, db, imp_service: ImportService, search_service: SearchService) -> int:
    """Автоматический прогон и отчёт по метрикам базового качества Уровня 1 (Раздел 9.1 ТЗ)."""
    seed_file = settings.data_dir / "seed_events.json"
    if seed_file.exists():
        imp_service.import_from_file(seed_file, actor="metrics_runner")

    # 1. Заполненность карточек (>= 95%)
    all_rows = db.fetchall("SELECT * FROM events")
    req_cols = ("title", "category", "date_start", "city", "venue", "address", "description", "source_url")
    filled = sum(
        1 for r in all_rows if all(r.get(c) is not None and str(r.get(c)).strip() != "" for c in req_cols)
    )
    completeness_pct = (filled / len(all_rows) * 100.0) if all_rows else 100.0

    # 2. Дубли в выдаче (<= 2%)
    from src.models.schemas import SearchFilters
    events_out, total_out, _ = search_service.search(SearchFilters(limit=500))
    dedup_keys = [e.dedup_key for e in events_out]
    dup_count = len(dedup_keys) - len(set(dedup_keys))
    dup_pct = (dup_count / len(events_out) * 100.0) if events_out else 0.0

    # 3 & 4 & 5. Полнота поиска (>= 90%), Пустая выдача (<= 5%), Скорость поиска p95 (<= 1.5 с)
    control_queries_with_data = [
        "Что интересного в Уфе в эти выходные?",
        "Концерт в Стерлитамаке в пятницу после 19:00 до 1500 рублей",
        "Куда пойти в субботу с ребёнком 8 лет в Стерлитамаке, до 1000 рублей на двоих, чтобы закончилось не позже 20:00",
        "Куда сходить с ребёнком 7 лет в субботу, не дольше двух часов и недорого",
        "Бесплатные выставки в Уфе до конца месяца",
        "Концерт в стерлитакмае в субботу",
        "Мероприятия на башкирском языке для детей",
        "Сколько стоит билет на ближайший балет?",
        "Что посмотреть сегодня вечером?",
        "Джаз в Уфе в воскресенье",
        "Спектакль в Салавате",
        "Концерт в Нефтекамске в субботу",
    ]
    latencies = []
    hits = 0
    false_empty = 0
    for q in control_queries_with_data:
        t0 = time.perf_counter()
        flt = parse_free_text_query(q, ref_date=settings.get_reference_date(), db=db)
        res, tot, _ = search_service.search(flt)
        elapsed = time.perf_counter() - t0
        latencies.append(elapsed)
        if tot > 0 and len(res) > 0:
            hits += 1
        else:
            false_empty += 1

    recall_pct = hits / len(control_queries_with_data) * 100.0
    empty_rate_pct = false_empty / len(control_queries_with_data) * 100.0
    latencies_sorted = sorted(latencies)
    p95_idx = min(len(latencies_sorted) - 1, int(len(latencies_sorted) * 0.95))
    p95_sec = latencies_sorted[p95_idx]

    # 6. Импорт файла на 100 событий без ручной правки и без дублей (100%)
    csv_100 = settings.data_dir / "import_100_events.csv"
    rep1 = imp_service.import_from_file(csv_100, actor="metrics_runner")
    rep2 = imp_service.import_from_file(csv_100, actor="metrics_runner")
    import_success_pct = ((rep1.added_count + rep1.updated_count + rep1.unchanged_count) / rep1.total_rows) * 100.0
    idempotent_ok = (rep2.added_count == 0 and rep2.rejected_count == 0 and rep2.unchanged_count == 100)

    print("=== ОТЧЁТ ПО МЕТРИКАМ БАЗОВОГО КАЧЕСТВА (РАЗДЕЛ 9.1 ТЗ) ===")
    print(f"1. Заполненность карточек: {completeness_pct:.1f}% (порог >= 95%) -> {'OK' if completeness_pct >= 95 else 'FAIL'}")
    print(f"2. Дубли в выдаче:         {dup_pct:.1f}% (порог <= 2%)  -> {'OK' if dup_pct <= 2 else 'FAIL'}")
    print(f"3. Полнота поиска:         {recall_pct:.1f}% (порог >= 90%) -> {'OK' if recall_pct >= 90 else 'FAIL'}")
    print(f"4. Пустая выдача:          {empty_rate_pct:.1f}% (порог <= 5%)  -> {'OK' if empty_rate_pct <= 5 else 'FAIL'}")
    print(f"5. Скорость поиска (p95):  {p95_sec:.4f} с (порог <= 1.5 с) -> {'OK' if p95_sec <= 1.5 else 'FAIL'}")
    print(f"6. Импорт 100 событий:     {import_success_pct:.1f}% без ошибок, идемпотентность={idempotent_ok} (порог 100%) -> {'OK' if import_success_pct == 100 and idempotent_ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
