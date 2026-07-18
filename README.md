# PostgreSQL Bot v7.1 — исправление JSONB

Исправлена ошибка, из-за которой игры зависали после сообщения
«Подготавливаю поле…».

Причина: asyncpg возвращал JSONB ежедневных заданий как строку.
Теперь для JSON и JSONB настроены кодеки json.dumps/json.loads.

Для обновления замените в GitHub:
- bot.py
- requirements.txt (можно оставить прежний, если там уже asyncpg)

После Commit changes дождитесь Deployment successful.
