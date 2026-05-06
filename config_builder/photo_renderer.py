"""Photo renderer: photo_map.json → config.sections.X.items."""

from pathlib import Path
from typing import Any, Dict, List

from config import settings


RADAR_PUBLIC_DIR = settings.public_dir


def get_block_photos(photo_map: Dict[str, Any], block: str) -> List[Dict[str, Any]]:
    """Извлекает массив фото блока из photo_map. Безопасный getter."""
    blocks = (photo_map or {}).get("blocks") or {}
    block_data = blocks.get(block) or {}
    if not block_data.get("enabled"):
        return []
    return block_data.get("photos") or []


def build_block_photos(
    photo_map: Dict[str, Any],
    block: str,
    slug: str,
    lead_id: int,
    public_dir: Path,
) -> List[Dict[str, Any]]:
    """
    Собирает массив фото для одного блока в формате шаблона.

    Возвращает список объектов:
    [
      {
        "src": "/mood/gallery/photo_1.jpg",
        "alt": "Окрашивание волос в блонд",
        "marketingGrade": 9,
        "fitScore": 10,
        "serviceRef": "coloring",
        "category": "work_result"
      },
      ...
    ]

    Сохраняем поля кроме src/alt — для расширений шаблона в будущем.
    """
    photos = get_block_photos(photo_map, block)
    if not photos:
        return []

    # Найти реальные файлы в public/{slug}-{lead_id}/{block}/
    folder = public_dir / f"{slug}-{lead_id}" / block
    if not folder.exists():
        # Возможно photo_map создан но фото не скачаны — пропускаем
        return []

    # Сортируем файлы детерминированно (как они лежат на диске)
    on_disk = sorted([
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    ])

    # Связываем фото из photo_map с файлами на диске.
    # photo_map.photos[] идут в порядке ranking. Файлы на диске — photo_1.jpg, photo_2.jpg, ...
    # Если кол-во не совпадает (что-то не скачалось) — берём минимум.
    n = min(len(photos), len(on_disk))
    result = []
    for i in range(n):
        p = photos[i]
        filename = on_disk[i].name  # photo_1.jpg или service-slug.jpg
        result.append({
            "src": f"/{slug}/{block}/{filename}",
            "alt": (p.get("alt") or "").strip() or f"Фото {i + 1}",
            # Bonus метаданные — для расширений шаблона
            "description": (p.get("description") or "").strip() or None,
            "marketingGrade": p.get("marketing_grade"),
            "fitScore": p.get("fit_score"),
            "category": p.get("category"),
            "serviceRef": p.get("service_ref"),
        })

    # Удаляем None-значения чтобы JS-файл был чище
    result = [{k: v for k, v in item.items() if v is not None} for item in result]
    return result


def get_hero_photo(
    photo_map: Dict[str, Any], slug: str, lead_id: int, public_dir: Path
) -> Dict[str, Any] | None:
    """Возвращает один объект для hero или None."""
    photos = build_block_photos(photo_map, "hero", slug, lead_id, public_dir)
    return photos[0] if photos else None
