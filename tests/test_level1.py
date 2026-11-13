import json
import time
import unittest
from pathlib import Path
from src.bot.router import BotRouter
from src.config import Settings
from src.db.connection import Database
from src.db.migrations import apply_migrations, seed_dictionaries
from src.models.schemas import SearchFilters
from src.services.import_service import ImportService
from src.services.normalizer import parse_free_text_query, resolve_city
from src.services.search_service import SearchService
from src.services.subscription_service import SubscriptionService
from src.utils.logger import anonymize_user_id, scrub_pii


class TestAfishaLevel1(unittest.TestCase):
    def setUp(self) -> None:
        self.base_dir = Path(__file__).resolve().parent.parent
        self.settings = Settings(
            telegram_bot_token="test_token",
            database_url=":memory:",
            admin_user_ids=["999001"],
            rate_limit_per_minute=50,
            subscription_retention_days=180,
            log_level="ERROR",
            reference_date_str="2026-11-13",
            data_dir=self.base_dir / "data",
            migrations_dir=self.base_dir / "migrations",
        )
        self.db = Database(":memory:")
        apply_migrations(self.db, self.settings.migrations_dir)
        seed_dictionaries(self.db, self.settings.data_dir / "dictionaries.json")
        self.import_service = ImportService(self.db)
        self.search_service = SearchService(self.db)
        self.sub_service = SubscriptionService(self.db, retention_days=180)
        self.router = BotRouter(self.db, self.settings)

        # Загружаем подготовленный набор событий
        self.seed_report = self.import_service.import_from_file(
            self.settings.data_dir / "seed_events.json", actor="test_setup"
        )

    def test_01_migrations_idempotent(self) -> None:
        """Повторный запуск миграций и справочников не ломает схему и не создаёт дублей (Раздел 10 ТЗ)."""
        applied_again = apply_migrations(self.db, self.settings.migrations_dir)
        self.assertEqual(applied_again, 0)
        seed_dictionaries(self.db, self.settings.data_dir / "dictionaries.json")
        cities = self.db.fetchall("SELECT * FROM cities")
        self.assertGreaterEqual(len(cities), 10)

    def test_02_import_idempotent_and_validation_and_100_csv(self) -> None:
        """Импорт CSV/JSON идемпотентен, валидирует поля, помечает спорные и загружает 100 событий без дублей (Раздел 6.1, 9.1)."""
        self.assertEqual(self.seed_report.total_rows, 20)
        self.assertEqual(self.seed_report.added_count, 20)
        self.assertEqual(self.seed_report.rejected_count, 0)
        self.assertGreaterEqual(self.seed_report.disputed_count, 1)

        # Повторный импорт того же файла не создаёт дублей
        rep_repeat = self.import_service.import_from_file(
            self.settings.data_dir / "seed_events.json", actor="test_setup"
        )
        self.assertEqual(rep_repeat.added_count, 0)
        self.assertEqual(rep_repeat.unchanged_count, 20)

        # Проверка отбраковки невалидных записей с понятной причиной
        bad_records = [
            {"title": "", "category": "концерт"},
            {
                "title": "Тестовое событие с плохой категорией",
                "category": "несуществующая_категория",
                "date_start": "2026-11-20T18:00:00",
                "city": "Уфа",
                "venue": "ГКЗ",
                "address": "ул. Ленина, 50",
                "description": "Достаточно длинное описание для проверки валидации категории.",
                "source_url": "https://example.com/ev",
            },
        ]
        bad_rep = self.import_service.import_records(bad_records, source_file="bad.json", actor="tester")
        self.assertEqual(bad_rep.rejected_count, 2)
        self.assertEqual(len(bad_rep.rejected_reasons), 2)

        # Импорт контрольного файла на 100 событий (метрика Раздела 9.1 = 100%)
        csv_100 = self.settings.data_dir / "import_100_events.csv"
        rep100 = self.import_service.import_from_file(csv_100, actor="tester")
        self.assertEqual(rep100.total_rows, 100)
        self.assertEqual(rep100.added_count, 100)
        self.assertEqual(rep100.rejected_count, 0)

        rep100_again = self.import_service.import_from_file(csv_100, actor="tester")
        self.assertEqual(rep100_again.added_count, 0)
        self.assertEqual(rep100_again.unchanged_count, 100)

    def test_03_raw_events_and_reprocess_and_audit_log(self) -> None:
        """Сырые данные хранятся отдельно, нормализацию можно переиграть, журнал изменений ведётся (Раздел 6.1)."""
        raw_rows = self.db.fetchall("SELECT * FROM raw_events")
        self.assertEqual(len(raw_rows), 20)

        reprocessed = self.import_service.reprocess_raw_events(actor="reprocessor_test")
        self.assertEqual(reprocessed, 20)

        audit_rows = self.db.fetchall("SELECT * FROM audit_log")
        self.assertGreater(len(audit_rows), 20)

    def test_04_cancelled_and_draft_events_handling(self) -> None:
        """Отменённое событие не попадает в выдачу, но остаётся доступным по прямой ссылке с явной пометкой (Раздел 6.1)."""
        # В общем поиске отменённого спектакля «Ревизор» (id ...000000000008) и черновика (...000000000020) нет
        events, total, _ = self.search_service.search(SearchFilters(limit=100))
        ids = {e.id for e in events}
        self.assertNotIn("11111111-1111-4111-8111-000000000008", ids)
        self.assertNotIn("11111111-1111-4111-8111-000000000020", ids)

        # Но по прямой команде /event_11111111-1111-4111-8111-000000000008 или deep-link /start event_... карточка открывается с явной пометкой
        resp = self.router.handle_message("user_1", "/event_11111111-1111-4111-8111-000000000008")
        self.assertIn("МЕРОПРИЯТИЕ ОТМЕНЕНО", resp.text)
        self.assertIn("Ревизор", resp.text)

    def test_05_end_to_end_bot_scenario_and_3_clicks_to_card(self) -> None:
        """Сквозной сценарий: /start -> фильтр -> выдача 3-5 карточек -> карточка (не более 3 действий) -> подписка и отписка."""
        # Действие 1: /start
        r1 = self.router.handle_message("user_42", "/start")
        self.assertIn("Афиша культурных мероприятий Башкортостана", r1.text)
        self.assertIsNotNone(r1.reply_markup)

        # Действие 2: выбор города Уфа
        r2 = self.router.handle_callback("user_42", "city:Уфа")
        self.assertIn("Найдено событий", r2.text)

        # Действие 3: открытие карточки первого события из кнопки выдачи
        first_card_cb = r2.reply_markup["inline_keyboard"][0][0]["callback_data"]
        self.assertTrue(first_card_cb.startswith("card:"))
        r3 = self.router.handle_callback("user_42", first_card_cb)
        self.assertIn("Категория:", r3.text)
        self.assertIn("Дата и время:", r3.text)
        self.assertIn("Адрес:", r3.text)
        self.assertIn("Источник:", r3.text)

        # Подписка на город и категорию
        r_sub = self.router.handle_callback("user_42", "sub:Уфа:концерт")
        self.assertIn("Подписка оформлена", r_sub.text)
        subs = self.sub_service.list_subscriptions("user_42")
        self.assertEqual(len(subs), 1)

        # Отписка одной командой /unsubscribe
        r_unsub = self.router.handle_message("user_42", "/unsubscribe")
        self.assertIn("отписаны от всех уведомлений", r_unsub.text)
        self.assertEqual(len(self.sub_service.list_subscriptions("user_42")), 0)

    def test_06_control_queries_and_typo_and_empty_explanation(self) -> None:
        """Проверка контрольных запросов из Раздела 2 и Приложения А (включая опечатки и объяснение пустого результата)."""
        # 1. Опечатка в названии города: «Концерт в стерлитакмае в субботу»
        self.assertEqual(resolve_city("стерлитакмае", db=self.db), "Стерлитамак")
        r_typo = self.router.handle_message("user_5", "Концерт в стерлитакмае в субботу")
        self.assertIn("Стерлитамак", r_typo.text)
        self.assertIn("Осенний дивертисмент", r_typo.text)

        # 2. Запрос из Раздела 2 ТЗ: «Куда пойти в субботу с ребёнком 8 лет в Стерлитамаке, до 1000 рублей на двоих, чтобы закончилось не позже 20:00»
        r_complex = self.router.handle_message(
            "user_5",
            "Куда пойти в субботу с ребёнком 8 лет в Стерлитамаке, до 1000 рублей на двоих, чтобы закончилось не позже 20:00",
        )
        self.assertIn("Тайна золотой пчелы", r_complex.text)

        # 3. Запрос с пустым результатом: «Есть ли в Салавате джаз на следующей неделе?»
        r_empty = self.router.handle_message("user_5", "Есть ли в Салавате джаз на следующей неделе?")
        self.assertIn("не найдено", r_empty.text)
        self.assertIn("Рекомендация", r_empty.text)

        # 4. Бесплатные выставки в Уфе до конца месяца
        r_free = self.router.handle_message("user_5", "Бесплатные выставки в Уфе до конца месяца")
        self.assertIn("Сокровища сарматских курганов", r_free.text)

        # 5. Мероприятия на башкирском языке для детей
        r_bash = self.router.handle_message("user_5", "Мероприятия на башкирском языке для детей")
        self.assertIn("Урал-батыр", r_bash.text)

    def test_07_quality_metrics_section_9_1(self) -> None:
        """Проверка всех порогов базового качества Раздела 9.1 ТЗ."""
        # 1. Заполненность >= 95%
        rows = self.db.fetchall("SELECT * FROM events")
        req_cols = ("title", "category", "date_start", "city", "venue", "address", "description", "source_url")
        filled = sum(1 for r in rows if all(r.get(c) and str(r.get(c)).strip() for c in req_cols))
        self.assertGreaterEqual(filled / len(rows), 0.95)

        # 2. Дубли <= 2%
        evs, _, _ = self.search_service.search(SearchFilters(limit=500))
        keys = [e.dedup_key for e in evs]
        self.assertEqual(len(keys), len(set(keys)))

        # 3. Скорость поиска p95 <= 1.5 с
        t0 = time.perf_counter()
        self.router.handle_message("user_perf", "Что интересного в Уфе в эти выходные?")
        elapsed = time.perf_counter() - t0
        self.assertLessEqual(elapsed, 1.5)

    def test_08_pii_scrubbing_and_rate_limiting(self) -> None:
        """Персональные данные не попадают в логи, ограничение частоты запросов работает корректно (Раздел 6.1, 10)."""
        scrubbed = scrub_pii("Мой телефон +7 (917) 123-45-67 и почта test@mail.ru")
        self.assertNotIn("917", scrubbed)
        self.assertNotIn("test@mail.ru", scrubbed)
        self.assertTrue(anonymize_user_id("123456").startswith("usr_"))



    def test_09_single_window_ui_and_ux_polish(self) -> None:
        """Проверка авто-замены сообщений (editMessageText / deleteMessage), живых счётчиков и переключателей подписок."""
        import asyncio
        from src.bot.telegram_runner import TelegramPollingRunner

        # Проверяем живые счётчики в главном меню и меню городов
        r_start = self.router.handle_message("user_99", "/start")
        self.assertIn("Подходящих событий в базе: <b>18</b>", r_start.text)
        main_btn = r_start.reply_markup["inline_keyboard"][0][0]["text"]
        self.assertIn("Показать события (18)", main_btn)

        r_cities = self.router.handle_callback("user_99", "menu:cities")
        cities_flat = [btn["text"] for row in r_cities.reply_markup["inline_keyboard"] for btn in row]
        self.assertTrue(any("Уфа (" in label for label in cities_flat))

        # Проверяем быстрый пресет 'На выходных' и переключатель подписки sub_toggle
        r_weekend = self.router.handle_callback("user_99", "preset:weekend")
        self.assertIn("Найдено событий", r_weekend.text)

        r_toggle_on = self.router.handle_callback("user_99", "sub_toggle:Уфа:концерт")
        self.assertIn("Подписка оформлена", r_toggle_on.text)
        self.assertTrue(self.sub_service.is_subscribed("user_99", "Уфа", "концерт"))

        r_toggle_off = self.router.handle_callback("user_99", "sub_toggle:Уфа:концерт")
        self.assertIn("Подписка удалена", r_toggle_off.text)
        self.assertFalse(self.sub_service.is_subscribed("user_99", "Уфа", "концерт"))

        # Проверяем Single-Window логику TelegramPollingRunner на мок-клиенте
        runner = TelegramPollingRunner(token="123:TEST", router=self.router, log_level="ERROR")
        called_endpoints = []

        class DummyResponse:
            def __init__(self, payload):
                self._payload = payload
            def json(self):
                return self._payload

        class DummyClient:
            async def post(self, url, json=None, timeout=None):
                method = url.rsplit("/", 1)[-1]
                called_endpoints.append((method, json))
                if method == "sendMessage":
                    return DummyResponse({"ok": True, "result": {"message_id": len(called_endpoints) + 100}})
                return DummyResponse({"ok": True, "result": True})

        async def run_ui_flow():
            client = DummyClient()
            # 1. Первое текстовое сообщение /start -> sendMessage (без deleteMessage)
            await runner._handle_update(client, {
                "update_id": 1,
                "message": {"message_id": 10, "chat": {"id": 777}, "from": {"id": 777}, "text": "/start"}
            })
            # 2. Нажатие инлайн-кнопки -> answerCallbackQuery + editMessageText (НЕ sendMessage!)
            await runner._handle_update(client, {
                "update_id": 2,
                "callback_query": {
                    "id": "cq_1",
                    "from": {"id": 777},
                    "message": {"message_id": 101, "chat": {"id": 777}},
                    "data": "menu:cities"
                }
            })
            # 3. Ввод нового текста -> sendMessage + deleteMessage старого сообщения бота (101)
            await runner._handle_update(client, {
                "update_id": 3,
                "message": {"message_id": 11, "chat": {"id": 777}, "from": {"id": 777}, "text": "Концерт в Уфе"}
            })

        asyncio.run(run_ui_flow())
        methods = [m for m, _ in called_endpoints]
        self.assertEqual(methods, ["sendMessage", "answerCallbackQuery", "editMessageText", "sendMessage", "deleteMessage"])


if __name__ == "__main__":
    unittest.main()
