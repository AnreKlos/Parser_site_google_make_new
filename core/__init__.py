"""
core/ — пайплайн парсинга одного донора.

Слой отвечает за: URL → fetch HTML → detect selectors → extract data
                  → normalize → save (JSON/Markdown).

Правило: модули в этом пакете НЕ знают о БД, модели Lead и не работают
с lead_id. Они принимают URL и возвращают чистые структуры данных.

Если функция требует чтения из БД — её место в services/.
См. README.md, раздел "🏗 Архитектура".
"""
from core.scraper import run_parse, ParseOptions, ParseResult, extract_data, normalize_data, build_markdown
from core.auto_detector import auto_detect
from core.config_manager import load_config, save_config
from core.block_flags import compute_block_flags

__all__ = [
    "run_parse", "ParseOptions", "ParseResult", "extract_data",
    "normalize_data", "build_markdown", "auto_detect",
    "load_config", "save_config", "compute_block_flags",
]
