import argparse
import hashlib
import json
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from src.bot.formatter import format_event_card, format_import_report, format_search_results
from src.config import load_settings
from src.db.connection import Database
from src.services.import_service import ImportService
from src.services.normalizer import parse_free_text_query
from src.services.search_service import SearchService


def init_db_and_services():
    settings = load_settings()
    db = Database(settings.database_url)
    return settings, db, ImportService(db), SearchService(db)


def init_sqlite_services(database_url: str):
    if not database_url.startswith("sqlite:///"):
        raise ValueError("Для изолированного запуска collect-gkz разрешена только SQLite")
    database_path = Path(database_url[len("sqlite:///"):]).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not database_path.is_relative_to(temp_root):
        raise ValueError(f"Путь SQLite должен находиться во временном каталоге {temp_root}")
    settings = load_settings()
    settings.database_url = database_url
    db = Database(database_url)
    return settings, db, ImportService(db), SearchService(db)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CLI-инструмент управления каталогом и модерации «Афиша культуры Башкортостана» (Уровень 1)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare", help="Применить миграции и загрузить справочники")
    sub.add_parser("prune", help="Удалить данные с истёкшим сроком хранения")
    sub.add_parser("hash-password", help="Создать PBKDF2-хеш пароля администратора")
    p_gkz = sub.add_parser("collect-gkz", help="Однократно собрать события с сайта ГКЗ")
    p_gkz.add_argument("--timeout", type=float, default=15.0, help="Таймаут HTTP-запроса в секундах")
    p_gkz.add_argument("--retries", type=int, default=2, help="Число повторов временных сетевых ошибок")
    p_gkz.add_argument("--actor", default="gkz_collector", help="Инициатор записи в журнал аудита")
    p_gkz.add_argument("--dry-run", action="store_true", help="Загрузить и проверить страницу без подключения к БД")
    p_gkz.add_argument("--database-url", help="Явный SQLite URL для изолированного импорта")
    p_venue = sub.add_parser("collect-venue", help="Собрать события с официальной афиши площадки Уфы")
    from src.services.venue_collector import BY_CODE as configured_venue_codes
    p_venue.add_argument("source", choices=sorted(configured_venue_codes))
    p_venue.add_argument("--dry-run", action="store_true", help="Проверить страницу без записи в БД")
    p_venue.add_argument("--retries", type=int, default=2, help="Число повторов временных сетевых ошибок")
    p_kassir = sub.add_parser("collect-kassir", help="Собрать события из афиши ufa.kassir.ru")
    p_kassir.add_argument("--dry-run", action="store_true", help="Проверить страницы без записи в БД")
    p_kassir.add_argument("--timeout", type=float, default=20.0, help="Таймаут HTTP-запроса в секундах")
    p_kassir.add_argument("--retries", type=int, default=2, help="Число повторов временных сетевых ошибок")

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
    p_agent = sub.add_parser("agent-metrics", help="Контрольный прогон агента по разделу 9.2 ТЗ")
    p_agent.add_argument("--rub-per-usd", type=float, default=None,
                         help="Курс для перевода известной стоимости API из USD в рубли")

    args = parser.parse_args()
    if args.command == "agent-metrics":
        return run_agent_metrics(load_settings(), args.rub_per_usd)
    if args.command == "hash-password":
        import getpass
        from src.services.admin_auth import hash_password
        password = getpass.getpass("Новый пароль: ")
        if len(password) < 16:
            parser.error("Пароль должен содержать не менее 16 символов")
        print(hash_password(password))
        return 0
    if args.command in {"prepare", "prune"}:
        from src.bootstrap import prepare, prune
        settings = load_settings()
        db = Database(settings.database_url)
        try:
            if args.command == "prepare":
                prepare(db, settings)
            else:
                prune(db, settings)
        finally:
            db.close()
        return 0
    if args.command == "collect-gkz" and args.dry_run:
        from src.services.gkz_collector import GKZCollector
        try:
            collector = GKZCollector(timeout=args.timeout, retries=args.retries)
        except ValueError as exc:
            parser.error(str(exc))
        try:
            result = collector.dry_run()
        finally:
            collector.close()
        print(result.message)
        if result.stats:
            stats = result.stats
            print(f"HTTP: {stats.get('http_status', '—')}; HTML: {stats.get('html_bytes', 0)} байт; карточек: {stats.get('cards_found', 0)}; разобрано: {stats.get('parsed', 0)}; отклонено: {stats.get('rejected', 0)}")
            for item in stats.get("rejected_reasons", []):
                print(f"Отклонение ({item['title']}): {item['reason']}")
        return 0 if result.ok else 1

    if args.command == "collect-venue" and args.dry_run:
        from src.services.venue_collector import BY_CODE, VenueCollector
        collector = VenueCollector(BY_CODE[args.source], retries=args.retries)
        try:
            result = collector.run()
        finally:
            collector.close()
        print(result.message)
        print(result.stats)
        return 0 if result.ok else 1

    if args.command == "collect-kassir" and args.dry_run:
        from src.services.kassir_collector import KassirCollector
        collector = KassirCollector(timeout=args.timeout, retries=args.retries)
        try:
            result = collector.run()
        finally:
            collector.close()
        print(result.message)
        print(result.stats)
        return 0 if result.ok else 1

    if args.command == "collect-gkz" and args.database_url:
        try:
            settings, db, imp_service, search_service = init_sqlite_services(args.database_url)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        settings, db, imp_service, search_service = init_db_and_services()

    if args.command == "collect-gkz":
        from src.services.gkz_collector import GKZCollector
        try:
            collector = GKZCollector(timeout=args.timeout, retries=args.retries)
        except ValueError as exc:
            parser.error(str(exc))
        try:
            result = collector.collect(imp_service, actor=args.actor)
        finally:
            collector.close()
            if args.database_url:
                db.close()
        print(result.message)
        if result.report:
            print(format_import_report(result.report))
        return 0 if result.ok else 1

    if args.command == "collect-venue":
        from src.services.venue_collector import BY_CODE, VenueCollector
        collector = VenueCollector(BY_CODE[args.source], retries=args.retries)
        try:
            result = collector.run(imp_service)
        finally:
            collector.close()
        print(result.message)
        print(result.stats)
        if result.report:
            print(format_import_report(result.report))
        return 0 if result.ok else 1

    if args.command == "collect-kassir":
        from src.services.kassir_collector import KassirCollector
        collector = KassirCollector(timeout=args.timeout, retries=args.retries)
        try:
            result = collector.run(imp_service)
        finally:
            collector.close()
        print(result.message)
        print(result.stats)
        if result.report:
            print(format_import_report(result.report))
        return 0 if result.ok else 1

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


def run_agent_metrics(settings, rub_per_usd: float | None = None) -> int:
    """Run primary and holdout evaluation sets against an isolated catalog."""
    from src.bootstrap import prepare
    from src.services.agent_service import AgentService
    from src.evaluation.agent_quality import (
        LEVEL4_THRESHOLDS, dataset_digest, evaluate_case, keyword_search,
        level4_gate, metric_summary, worst_metric, write_report,
    )
    from src.agent.heavy_llm import _SYSTEM as heavy_prompt
    from src.agent.jev import _SYSTEM_PROMPT as router_prompt
    from src.agent.light_llm import _PROMPT as light_prompt
    from src.agent.smart import _PROMPT as smart_prompt
    from src.agent.structured_deep import _PROMPT as comparison_prompt
    protected_prompts = (heavy_prompt, router_prompt, light_prompt, smart_prompt, comparison_prompt)
    primary_path = Path("data/eval_level3_queries.json")
    holdout_path = Path("data/eval_external_holdout.json")
    primary_cases = json.loads(primary_path.read_text(encoding="utf-8"))
    holdout_cases = json.loads(holdout_path.read_text(encoding="utf-8"))
    for label, cases, minimum in (("primary", primary_cases, 60), ("holdout", holdout_cases, 10)):
        if len(cases) < minimum or len({case.get("id") for case in cases}) != len(cases):
            raise ValueError(f"Invalid {label} evaluation set: minimum size or unique IDs violated")
    if sum(c.get("kind") == "injection" for c in primary_cases) < 10:
        raise ValueError("Primary set must include at least 10 prompt injection cases")
    db = Database(":memory:")
    try:
        prepare(db, settings, seed_demo=True)
        agent = AgentService(db, settings)
        catalog_rows = db.fetchall("SELECT * FROM events WHERE status='published' AND merged_into IS NULL ORDER BY date_start, id")
        catalog = {str(row["id"]): row for row in catalog_rows}
        all_events = list(catalog.values())
        active = db.fetchone("SELECT id, description FROM events WHERE status='published' AND merged_into IS NULL ORDER BY id LIMIT 1")

        def evaluate_set(label: str, cases: list[dict]) -> tuple[list[dict], list[dict]]:
            agent_rows, baseline_rows = [], []
            for case in cases:
                injecting = case.get("injection_surface") == "event_description"
                if injecting and active:
                    db.execute("UPDATE events SET description=? WHERE id=?", (case["injection_text"], active["id"]))
                try:
                    conversation_id = f"eval:{label}:{case['id']}"
                    started = time.perf_counter()
                    result = agent.chat(case["query"], conversation_id, user_id=conversation_id,
                                        request_id="eval-" + uuid.uuid4().hex)
                    trace = agent.get_trace(result.request_id)
                    row = evaluate_case(case, result.response, trace, catalog, result.status, protected_prompts)
                    row["latency_seconds"] = time.perf_counter() - started
                    row.update(dialogue_id=conversation_id, error=result.status == "error" or trace is None)
                    agent_rows.append(row)
                except Exception as exc:
                    agent_rows.append({"id": case.get("id"), "kind": case.get("kind"), "status": "error",
                                       "factual_accuracy": "UNVERIFIED", "hallucinations": 0,
                                       "constraint_satisfaction": None, "clarification": None,
                                       "honest_refusal": None, "tool_accuracy": None,
                                       "injection_pass": False if case.get("kind") == "injection" else None,
                                       "trace_complete": False, "latency_seconds": None,
                                       "input_tokens": None, "output_tokens": None, "cost_usd": None,
                                       "error": type(exc).__name__, "dialogue_id": f"eval:{label}:{case.get('id')}"})
                finally:
                    if injecting and active:
                        db.execute("UPDATE events SET description=? WHERE id=?", (active["description"], active["id"]))
                started = time.perf_counter()
                found = keyword_search(case["query"], all_events)
                baseline_response = {"message": "", "event_cards": found}
                baseline_row = evaluate_case(case, baseline_response, None, catalog, "ok" if found else "no_data")
                baseline_row["latency_seconds"] = time.perf_counter() - started
                baseline_row["dialogue_id"] = f"baseline:{label}:{case.get('id')}"
                baseline_row["cost_usd"] = None
                baseline_row["input_tokens"] = baseline_row["output_tokens"] = None
                baseline_row["trace_complete"] = False
                baseline_rows.append(baseline_row)
            return agent_rows, baseline_rows

        primary_rows, primary_baseline = evaluate_set("primary", primary_cases)
        holdout_rows, holdout_baseline = evaluate_set("holdout", holdout_cases)

        # Twenty requests share a single conversation, so state leakage/context regressions are visible.
        sequential_id = "eval:stability:" + uuid.uuid4().hex
        sequential_statuses, sequential_traces = [], []
        for index in range(20):
            case = primary_cases[index % len(primary_cases)]
            try:
                result = agent.chat(case["query"], sequential_id, user_id=sequential_id,
                                    request_id="eval-seq-" + uuid.uuid4().hex)
                trace = agent.get_trace(result.request_id)
                sequential_statuses.append(result.status)
                if trace:
                    sequential_traces.append(trace)
            except Exception:
                sequential_statuses.append("error")

        # A separate two-turn scenario measures whether a follow-up uses prior results.
        context_id = "eval:context:" + uuid.uuid4().hex
        context_setup_ok = context_followup_ok = False
        context_query = next((c for c in primary_cases if c.get("kind") == "context"), None)
        if context_query:
            try:
                first = agent.chat("Что интересного в Уфе в эти выходные?", context_id, user_id=context_id,
                                   request_id="eval-context-setup-" + uuid.uuid4().hex)
                second = agent.chat(context_query["query"], context_id, user_id=context_id,
                                    request_id="eval-context-followup-" + uuid.uuid4().hex)
                context_setup_ok = first.status != "error"
                context_followup_ok = second.status != "error" and bool(second.response.event_cards or second.response.message)
            except Exception:
                pass

        primary_metrics = metric_summary(primary_rows, rub_per_usd)
        holdout_metrics = metric_summary(holdout_rows, rub_per_usd)
        primary_baseline_metrics = metric_summary(primary_baseline, rub_per_usd)
        holdout_baseline_metrics = metric_summary(holdout_baseline, rub_per_usd)
        worst = worst_metric(primary_metrics, holdout_metrics)
        metric_failures = {}
        for set_name, metrics in (("primary", primary_metrics), ("holdout", holdout_metrics), ("worst", worst)):
            gate = level4_gate(metrics)
            metric_failures[set_name] = [
                {"metric": key, "actual": metrics.get(key), "required": threshold,
                 "reason": "UNMEASURED" if gate[key] is None else "THRESHOLD_NOT_MET"}
                for key, threshold in LEVEL4_THRESHOLDS.items()
                if gate[key] is not True
            ]
        run_date = datetime.now(timezone.utc).isoformat(timespec="seconds")
        models = sorted({str(model) for row in primary_rows + holdout_rows
                         for model in (row.get("selected_models") or [row.get("model_name")]) if model})
        if not models:
            models = [settings.light_model, settings.heavy_model]
        stability_cost_usd = (sum(float(trace.cost) for trace in sequential_traces)
                              if len(sequential_traces) == 20 and all(trace.cost is not None for trace in sequential_traces)
                              else None)
        stability_dialogue = {
            "input_tokens": (sum(trace.input_tokens for trace in sequential_traces)
                             if len(sequential_traces) == 20 and all(trace.llm_call_count > 0 for trace in sequential_traces)
                             else None),
            "output_tokens": (sum(trace.output_tokens for trace in sequential_traces)
                              if len(sequential_traces) == 20 and all(trace.llm_call_count > 0 for trace in sequential_traces)
                              else None),
            "cost_usd": stability_cost_usd,
            "cost_rub": (stability_cost_usd * rub_per_usd if stability_cost_usd is not None and rub_per_usd and rub_per_usd > 0 else None),
        }
        report = {
            "run_date": run_date, "ai_mode": settings.ai_mode, "model": models,
            "evaluation_type": "INFRASTRUCTURE_ONLY" if settings.ai_mode == "no-ai" else "MODEL_QUALITY",
            "prompt_version": settings.jev_prompt_version,
            "prompt_fingerprints": {name: hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                                    for name, prompt in (("heavy", heavy_prompt), ("router", router_prompt),
                                                         ("light", light_prompt), ("smart", smart_prompt),
                                                         ("comparison", comparison_prompt))},
            "reference_date": settings.get_reference_date().isoformat(),
            "datasets": {"primary": {"path": str(primary_path), "count": len(primary_cases), "sha256": dataset_digest(primary_path)},
                         "holdout": {"path": str(holdout_path), "count": len(holdout_cases), "sha256": dataset_digest(holdout_path)}},
            "rub_per_usd": rub_per_usd,
            "primary": primary_metrics, "holdout": holdout_metrics, "worst": worst,
            "primary_gate": level4_gate(primary_metrics), "holdout_gate": level4_gate(holdout_metrics),
            "worst_gate": level4_gate(worst),
            "metric_failures": metric_failures,
            "baseline": {"method": "casefolded Russian token overlap; title/description/category/city/venue/address; top 5",
                         "primary": primary_baseline_metrics, "holdout": holdout_baseline_metrics},
            "stability": {"requests": 20, "ok": sum(s != "error" for s in sequential_statuses),
                          "errors": [i for i, status in enumerate(sequential_statuses) if status == "error"],
                          "same_conversation": True, "dialogue_usage": stability_dialogue},
            "multi_step_context": {"setup_ok": context_setup_ok, "followup_ok": context_followup_ok,
                                   "status": "PASS" if context_setup_ok and context_followup_ok else "FAIL"},
            "cases": {"primary": primary_rows, "holdout": holdout_rows},
            "unverified_text_claims": "Response prose is not automatically classified; manual annotation required.",
        }
        json_path, md_path = write_report(report, Path("reports/agent-evaluation"))
        print(f"Level 4 evaluation: {settings.ai_mode}; primary={len(primary_rows)}; holdout={len(holdout_rows)}")
        print(f"Reports: {json_path} ; {md_path}")
        print(f"Worst metrics: {json.dumps(worst, ensure_ascii=False, default=str)}")
        print(f"20 sequential: {report['stability']['ok']}/20; multi-step context: {report['multi_step_context']['status']}")
        return 0 if all(value is True for value in report["worst_gate"].values()) else 1
    finally:
        db.close()


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
    checks = {
        "completeness": completeness_pct >= 95,
        "duplicates": dup_pct <= 2,
        "recall": recall_pct >= 90,
        "empty_rate": empty_rate_pct <= 5,
        "latency": p95_sec <= 1.5,
        "import": import_success_pct == 100 and idempotent_ok,
    }
    print(f"1. Заполненность карточек: {completeness_pct:.1f}% (порог >= 95%) -> {'OK' if checks['completeness'] else 'FAIL'}")
    print(f"2. Дубли в выдаче:         {dup_pct:.1f}% (порог <= 2%)  -> {'OK' if checks['duplicates'] else 'FAIL'}")
    print(f"3. Полнота поиска:         {recall_pct:.1f}% (порог >= 90%) -> {'OK' if checks['recall'] else 'FAIL'}")
    print(f"4. Пустая выдача:          {empty_rate_pct:.1f}% (порог <= 5%)  -> {'OK' if checks['empty_rate'] else 'FAIL'}")
    print(f"5. Скорость поиска (p95):  {p95_sec:.4f} с (порог <= 1.5 с) -> {'OK' if checks['latency'] else 'FAIL'}")
    print(f"6. Импорт 100 событий:     {import_success_pct:.1f}% без ошибок, идемпотентность={idempotent_ok} (порог 100%) -> {'OK' if checks['import'] else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
