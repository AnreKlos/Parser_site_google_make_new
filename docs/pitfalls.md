# Известные ловушки

## BASE_DIR в подпакетах

При создании файлов в `services/`, `llm/`, `core/` и т.д. корень
проекта — `Path(__file__).parent.parent`, **не** `parent`.

```python
# ✅ Правильно (из services/foo.py):
BASE_DIR = Path(__file__).parent.parent

# ❌ Неправильно — даст services/data/, а не data/:
BASE_DIR = Path(__file__).parent
```

**Лучше — импортируй `settings`:**
```python
from config import settings
db_path = settings.data_dir / "leads.db"
```

## Vertex AI / Gemini

- Требует `gcloud auth application-default login` перед стартом.
- Проект: `leadparser-491719`, регион `us-central1`.
- Модель: `gemini-2.5-flash-lite`.
- Используется в `llm/curator.py` и `llm/content_curator.py`.

## БД: cascade vs SET NULL

`Lead.audit_logs` имеет `cascade="all, delete-orphan"` —
при удалении лида логи удаляются вместе с ним. На стороне БД
есть `ON DELETE SET NULL`, но он не срабатывает (cascade
выполняется первым). См. README → Known Technical Debt.

## Subprocess из admin_dashboard

Большинство subprocess-вызовов в дашборде заменены на прямой импорт
(Шаги 3.5-3.6). Если видишь `subprocess.run(["python", ".py"]`
для модулей внутри проекта — это технический долг, надо заменять.
Исключение: `run_config_builder` — пока остаётся через subprocess.

## DEBUG-print при импорте

При импорте `services` (через `config.py` или `llm/llm_engine.py`)
в stdout печатается `[DEBUG] OPENROUTER_API_KEY loaded`. Это шум,
надо убрать. Низкий приоритет.