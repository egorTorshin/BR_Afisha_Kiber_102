# Уровень 2: приёмка веб-контура

- Compose: `db → prepare → api → frontend` запускаются без ручных миграций.
- Публичный сценарий: главная, каталог, фильтры, карточка, feedback и пустая выдача.
- Админка: login/logout, CSRF, роли, импорт, смена статуса, редактирование и аудит.
- Виджет: `/embed` в iframe шириной 360 px, параметры города, категории, периода и высоты.
- Проверки: backend pytest, PostgreSQL pytest, `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`, `npm run smoke:embed`.
- Для входа в веб-админку нужно задать `ADMIN_PASSWORD` или `ADMIN_PASSWORD_HASH` в `.env`; это отдельная от Telegram-администратора настройка.
