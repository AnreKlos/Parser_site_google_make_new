"""CLI entry-point and UI-friendly wrapper for config builder."""

import argparse
import json
import sys
import time
from pathlib import Path

from config import settings
from utils import slugify_name

from config_builder.builder import build_config
from config_builder.io import copy_public_assets, load_lead, log
from config_builder.js_export import to_js_module, validate_js_with_node

NEURALSYNC_CONFIGS_DIR = Path(settings.neuralsync_root) / "src" / "configs"


def _render_and_write(lead_id: int, slug: str) -> Path:
    """Build config, serialise to JS, validate, write to neuralsync."""
    config = build_config(lead_id)
    NEURALSYNC_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    config_path = NEURALSYNC_CONFIGS_DIR / f"{slug}-{lead_id}.config.js"

    js_module = to_js_module(config, slug)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(js_module)

    validate_js_with_node(config_path)
    log("✓ Валидация JS пройдена")
    log(f"📝 Записан {config_path}")
    return config_path


def run_build(lead_id: int, dry_run: bool = False, no_copy: bool = False) -> int:
    """Full build pipeline.

    Returns 0 on success, 1 on failure (CLI-friendly exit code).
    """
    started = time.perf_counter()
    log(f"🎯 Старт сборки lead_id={lead_id}")

    lead = load_lead(lead_id)
    if not lead:
        log(f"❌ Лид ID={lead_id} не найден")
        return 1

    slug = slugify_name(str(lead.get("name") or ""), lead_id)

    try:
        config = build_config(lead_id)
    except Exception as exc:
        log(f"❌ Ошибка сборки конфига: {exc}")
        return 1

    if dry_run:
        log("🧪 Dry-run: показываю собранный конфиг (без записи)")
        print(json.dumps(config, ensure_ascii=False, indent=2))
        elapsed = time.perf_counter() - started
        log(f"✅ Готово, время {elapsed:.1f} сек")
        return 0

    try:
        _render_and_write(lead_id, slug)
    except Exception as exc:
        log(f"❌ Ошибка записи/валидации: {exc}")
        return 1

    copied_files = copy_public_assets(slug, no_copy=no_copy)
    if not no_copy:
        log(f"📂 Скопировано {copied_files} фото в {Path(settings.neuralsync_root) / 'public' / slug}")

    elapsed = time.perf_counter() - started
    log(f"✅ Готово, время {elapsed:.1f} сек")
    return 0


def run_build_for_ui(lead_id: int, no_copy: bool = False) -> tuple:
    """UI-friendly wrapper — returns ``(success: bool, message: str)``.

    Designed for direct import from ``ui/admin_dashboard.py``.
    Never raises; returns ``(False, error_message)`` on failure.
    """
    try:
        code = run_build(lead_id, dry_run=False, no_copy=no_copy)
        if code == 0:
            return True, f"✅ Сборка lead_id={lead_id} завершена успешно"
        return False, f"❌ Сборка lead_id={lead_id} завершилась с ошибкой (код {code})"
    except Exception as exc:
        return False, f"❌ Ошибка сборки lead_id={lead_id}: {exc}"


def main() -> None:
    """CLI entry-point: ``python -m config_builder <lead_id> [--dry-run] [--no-copy]``."""
    parser = argparse.ArgumentParser(description="Config Builder for neuralsync")
    parser.add_argument("lead_id", type=int, help="Lead ID")
    parser.add_argument("--dry-run", action="store_true", help="Собрать и показать конфиг без записи файлов")
    parser.add_argument("--no-copy", action="store_true", help="Не копировать папку public/<slug>")
    args = parser.parse_args()

    code = run_build(args.lead_id, dry_run=args.dry_run, no_copy=args.no_copy)
    sys.exit(code)


if __name__ == "__main__":
    main()
