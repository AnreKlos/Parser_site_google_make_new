"""Card-bundle loader: card.json + photo_map.json + qualification."""

from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional
import json

from config import settings
from utils.text import slugify_name


YANDEX_DIR = settings.data_dir / "yandex"


class CardBundle(NamedTuple):
    """All data for a single lead, loaded from disk."""
    lead_id: int
    slug: str
    card: Dict[str, Any]              # card.json (schema v2)
    photo_map: Dict[str, Any]         # photo_map.json (schema v2)
    qualification: Dict[str, Any]     # из card.qualification
    available: bool                   # есть ли card.json вообще
    error: Optional[str]              # ошибка чтения, если any


def load_card_bundle(lead_id: int, lead_name: str) -> CardBundle:
    """
    Загружает все данные для лида из data/yandex/{slug}-{lead_id}/.

    Args:
        lead_id: PK из БД
        lead_name: Lead.name из БД (для slugify)

    Returns:
        CardBundle с полями. Если card.json отсутствует — available=False.
    """
    slug = slugify_name(lead_name, lead_id)
    lead_dir = YANDEX_DIR / f"{slug}-{lead_id}"
    card_path = lead_dir / "card.json"
    photo_map_path = lead_dir / "photo_map.json"

    if not card_path.exists():
        return CardBundle(
            lead_id=lead_id, slug=slug,
            card={}, photo_map={}, qualification={},
            available=False,
            error=f"card.json не найден: {card_path}",
        )

    try:
        with open(card_path, "r", encoding="utf-8") as f:
            card = json.load(f)
    except Exception as e:
        return CardBundle(
            lead_id=lead_id, slug=slug,
            card={}, photo_map={}, qualification={},
            available=False,
            error=f"Не удалось прочитать card.json: {e}",
        )

    photo_map = {}
    if photo_map_path.exists():
        try:
            with open(photo_map_path, "r", encoding="utf-8") as f:
                photo_map = json.load(f)
        except Exception as e:
            # photo_map не критичен — может быть лид без фото
            photo_map = {"blocks": {}, "_load_error": str(e)}

    qualification = card.get("qualification") or {}

    return CardBundle(
        lead_id=lead_id, slug=slug,
        card=card, photo_map=photo_map, qualification=qualification,
        available=True, error=None,
    )


def load_curated_optional(slug: str, lead_id: int) -> Dict[str, Any]:
    """
    Опционально читает data/curated/{slug}-{lead_id}.json (AI-сгенерированные тексты).
    Возвращает {} если файл не найден или невалиден.

    Curated даёт нам:
    - meta.tagline (для hero подзаголовка)
    - about (длинный текст для блока О нас)
    - faq[] (вопрос-ответ)
    """
    curated_path = settings.data_dir / "curated" / f"{slug}-{lead_id}.json"
    if not curated_path.exists():
        return {}
    try:
        with open(curated_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
