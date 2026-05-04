# Соглашения проекта#

## Пути и БД#

- **БД:** `data/leads.db` (SQLite, async через aiosqlite)
- **Курированный контент:** `data/curated/{slug}-{lead_id}.json`
- **Извлечённые блоки:** `data/extracted/{slug}-{lead_id}.json`
- **Данные Яндекса:** `data/yandex/{slug}-{lead_id}.json`
- **Конфиги сайтов:** `D:\2 Clode Proj\1\neuralsync\src\configs\{slug}-{lead_id}.config.js`
- **Фото лидов:** `public/{slug}/gallery|services|team|hero/`

## Slug#

`{slug}` генерируется через `slugify_name(name, lead_id)` —
транслит русского + замена не-alphanumeric на дефисы.
Используется как идентификатор лида в файловой системе.

## Async / Sync#

- Большинство модулей в `services/` и `llm/` — async.
- Из синхронного кода (Streamlit) вызывать через `asyncio.run(func(...))`.
- НЕ запускать через subprocess — использовать прямой импорт.

## Конфигурация#

Используй `from config import settings`:
- `settings.data_dir`, `settings.public_dir`, `settings.root_dir`
- `settings.openrouter_api_key`, `settings.openrouter_model`
- Не парси `os.environ` напрямую.

## Коммиты#

Conventional Commits:
- `refactor:` — рефакторинг без изменения логики
- `feat:` — новая фича
- `chore:` — рутина (зависимости, скрипты)
- `docs:` — документация
- `fix:` — исправление бага

Тело коммита — буллет-список ключевых изменений.