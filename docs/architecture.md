# Архитектура проекта

Проект организован в слои-пакеты. Каждый слой имеет чёткую
ответственность.

## Правило размещения

| Слой | Ответственность | Что туда кладём | Что НЕ кладём |
|------|-----------------|-----------------|---------------|
| `core/` | Парсинг **одного донора**: URL → data | Скрейперы, детекторы селекторов, парсеры блоков | Работу с БД, lead_id |
| `services/` | Бизнес-логика над БД | Радар, аудитор, питч-генератор, block_extractor | Чистый парсинг URL без БД |
| `llm/` | Только LLM-взаимодействие | OpenRouter engine, Vertex AI кураторы | Скрейпинг, обогащение из не-LLM |
| `db/` | Слой данных | SQLAlchemy модели, async-сессии | Бизнес-логику |
| `utils/` | Чистые утилиты | slugify, фильтры текста, JSON-парсеры | Что-то с БД, LLM или сетью |
| `migrations/` | Миграции БД | Ручные скрипты (план: Alembic) | — |
| `ui/` | Интерфейсы | Streamlit-дашборд, PySide6 GUI | Бизнес-логику |

## Тест "куда положить файл"

- Принимает `url`, возвращает `dict` → **`core/`**
- Принимает `lead_id`, пишет в БД → **`services/`**
- Вызывает LLM API → **`llm/`**
- Чистая функция без сети и БД → **`utils/`**

## Текущий состав

### core/
- `scraper.py` — главный пайплайн парсинга
- `auto_detector.py` — детектит структуру донора
- `config_manager.py` — per-domain JSON-конфиги
- `block_flags.py` — флаги наличия блоков

### services/
- `google_radar.py` — поиск через Google Places API
- `xray_auditor.py` — аудит сайтов (async batch)
- `pitch_builder.py` — генерация продающих сообщений
- `site_config_generator.py` — финальный JSON-конфиг
- `block_extractor.py` — извлечение услуг/FAQ/команды (Playwright)

### llm/
- `llm_engine.py` — клиент OpenRouter
- `curator.py` — Vertex AI / Gemini кастомный куратор (бывш. gemma_curator)
- `content_curator.py` — курация контента (слоган, About, отзывы, FAQ)
- `yandex_enricher.py` — обогащение с Яндекс.Карт *(не-LLM, переедет в enrichment/ — Шаг 4)*
- `photo_fetcher.py` — скачивание фото *(не-LLM, переедет в enrichment/ — Шаг 4)*

### db/
- `models.py` — `Lead`, `AuditLog`
- `database.py` — async-сессии, CRUD, пагинация, логирование
- `__init__.py` — реэкспорт публичного API

### Корневой уровень (точки входа)
- `config.py` — централизованные настройки (Pydantic Settings)
- `config_builder.py` — сборка финального JSON для neuralsync
- `auto_builder.py` — конвейер end-to-end
- `reset_pitches.py` — утилита