# TZ Stage 6: Native Builder v2 — single source of truth

> **Цель:** переписать builder натив��о на новый формат данных. Без адаптеров, без двух режимов. Один путь данных от парсера до лендинга.
>
> **Источник истины:** `data/yandex/{slug}-{lead_id}/card.json` (parser v2) + `data/yandex/{slug}-{lead_id}/photo_map.json` (photos_v2). Curated AI-тексты остаются как опциональный enricher.
>
> **Принципы:**
> - Никакого парсинга грязных данных — на входе только чистый card.json
> - Старый `builder.py` остаётся как fallback (не удаляется)
> - Новый `builder_v2.py` запускается через `python -m config_builder.cli {id} --v2`
> - Шаблон неruralsync остаётся обратно-совместимым (gallery.items может быть строкой или объектом — обе формы работают)
> - ASCII-only имена файлов

---

## Архитектура

```
┌──────────────────┐
│ data/yandex/     │     ┌─────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  {slug}-{id}/    │ ──► │ card_loader.py  │ ──► │ builder_v2.py    │ ──► │ neuralsync/      │
│   card.json      │     │ load_card_      │     │ build_config_v2  │     │  src/configs/    │
│   photo_map.json │     │  bundle()       │     │  ()              │     │   {slug}-{id}.   │
└──────────────────┘     └─────────────────┘     └──────────────────┘     │   config.js      │
                                                          │                └──────────────────┘
                                                          ▼
┌──────────────────┐     ┌─────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│ data/curated/    │ ──► │ optional        │     │ photo_renderer   │ ──► │ neuralsync/      │
│  {slug}-{id}.json│     │ enricher        │     │ .py              │     │  public/{slug}/  │
│ (AI-tagline,     │     │ (taglines, FAQ) │     │ build_block_     │     │   hero/...       │
│  AI-FAQ)         │     │                 │     │  photos()        │     │   gallery/...    │
└──────────────────┘     └─────────────────┘     └──────────────────┘     └──────────────────┘
```

**Старый pipeline остаётся доступным** через `python -m config_builder.cli {id}` (без `--v2`). Новый pipeline ничего не ломает.

---

## Part A — `enrichment/card_loader.py` (новый модуль)

Один источник правды. Все читатели данных идут через него.

### A.1 Структура

```python
"""Card-bundle loader: card.json + photo_map.json + qualification."""

from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional
import json

from config import settings
from utils import slugify_name


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
```

---

## Part B — `config_builder/photo_renderer.py` (новый модуль)

Превращает `photo_map.blocks.X.photos[]` (с rich-метаданными) в массивы для шаблона неуralsync.

### B.1 Pаты файлов

Шаблон ждёт `/{slug}/folder/file.jpg` (БЕЗ `-{lead_id}`). У нас фото лежат в `public/{slug}-{lead_id}/folder/file.jpg`. Будем копировать с `-{lead_id}` → без id (см. Part E).

### B.2 Структура

```python
"""Photo renderer: photo_map.json → config.sections.X.items."""

from pathlib import Path
from typing import Any, Dict, List


def web_path_for_photo(slug: str, lead_id: int, block: str, photo_url: str) -> str:
    """
    Преобразует Yandex CDN URL в локальный web-путь, как ожидает шаблон.

    Шаблон ждёт: /{slug}/{block}/photo_N.jpg
    После copy_public_assets_v2 файлы будут в public/{slug}/{block}/.
    Имена файлов сохраняются такими же, как в public/{slug}-{lead_id}/{block}/.

    Здесь мы не знаем имя файла на диске напрямую — оно строится во время
    скачивания photos_v2. Возвращаем ссылку с индексом по позиции в blocks.
    """
    # Имена файлов в photos_v2 — photo_1.jpg, photo_2.jpg или slug-of-service.jpg
    # для services. Передаём имя через caller: см. build_block_photos.
    raise NotImplementedError("Используй build_block_photos — там есть filename")


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
        "marketing_grade": 9,
        "fit_score": 10,
        "service_ref": "coloring",
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
    # photo_map.photos[] идут в порядке raнking. Файлы на диске — photo_1.jpg, photo_2.jpg, ...
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
```

---

## Part C — `config_builder/builder_v2.py` (новый главный модуль)

Заменяет ~1000 строк старого `builder.py`. Простой, читает только card.json + photo_map.json.

### C.1 Структура

```python
"""Native config builder v2 — works directly with card.json + photo_map.json."""

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings
from enrichment.card_loader import CardBundle, load_card_bundle, load_curated_optional
from utils import detect_city, slugify_name

from config_builder.defaults import (
    DEFAULT_CHAT_WIDGET, DEFAULT_CONTENT, DEFAULT_LEGAL,
    DEFAULT_SECTION_ORDER, DEFAULT_TOKENS,
)
from config_builder.io import load_lead, log
from config_builder.photo_renderer import build_block_photos, get_hero_photo


PUBLIC_DIR = settings.public_dir


# ======================================================================
# Public entry-point
# ======================================================================

def build_config_v2(lead_id: int) -> Dict[str, Any]:
    """
    Сборка config-объекта для шаблона neuralsync, на основе card.json+photo_map.json.

    Raises:
        RuntimeError: если лид не найден в БД или card.json отсутствует.
    """
    lead = load_lead(lead_id)
    if not lead:
        raise RuntimeError(f"Лид ID={lead_id} не найден в БД")

    lead_name = str(lead.get("name") or f"Lead {lead_id}")
    bundle = load_card_bundle(lead_id, lead_name)
    if not bundle.available:
        raise RuntimeError(
            f"card.json не найден для lead_id={lead_id}. "
            f"Запусти `python -m enrichment.yandex_state {lead_id} --force` сначала. "
            f"Деталь: {bundle.error}"
        )

    log(f"📖 [v2] Читаю card.json: services={len(bundle.card.get('services') or [])}, "
        f"aspects={len(bundle.card.get('aspects') or [])}, "
        f"reviews={len(bundle.card.get('reviews_preview') or [])}")

    qualification = bundle.qualification
    if not qualification.get("qualified"):
        log(f"⚠️  [v2] Лид НЕ КВАЛИФИЦИРОВАН: {qualification.get('reason')}")
        log(f"⚠️  [v2] Конфиг будет собран, но шаблон может выглядеть пусто")

    # Опциональные curated данные (AI-tagline, AI-FAQ)
    curated = load_curated_optional(bundle.slug, lead_id)
    if curated:
        log(f"📖 [v2] Подключён curated: tagline, faq={len(curated.get('faq') or [])}")

    # === Сборка блоков ===
    meta = _build_meta(bundle, curated)
    contacts = _build_contacts(bundle, lead)
    sections = _build_sections(bundle, lead, curated)
    block_flags = _build_block_flags(sections)

    config: Dict[str, Any] = {
        "meta": meta,
        "contacts": contacts,
        "booking": _build_booking(bundle),
        "social": _build_social(bundle, lead),
        "tokens": DEFAULT_TOKENS,
        "legal": DEFAULT_LEGAL,
        "content": DEFAULT_CONTENT,
        "sectionsOrder": _build_section_order(sections),
        "copyrightYear": datetime.now().year,
        "sections": sections,
        "features": {"chatWidget": DEFAULT_CHAT_WIDGET},
        "block_flags": block_flags,
        "_meta": {
            "builder_version": "2.0",
            "card_schema": bundle.card.get("schema_version"),
            "qualification": qualification,
        },
    }

    return config


# ======================================================================
# Block builders
# ======================================================================

def _build_meta(bundle: CardBundle, curated: Dict[str, Any]) -> Dict[str, Any]:
    """meta block: brand, tagline, city, награды."""
    card = bundle.card
    name = (card.get("title") or card.get("short_title") or "").strip()
    short_name = _strip_brand_noise(name)
    address = card.get("address") or {}
    city = address.get("locality") or detect_city(address.get("full") or "")

    # Tagline: curated > Yandex-derived
    curated_tagline = ((curated.get("meta") or {}).get("tagline") or "").strip()
    if curated_tagline:
        tagline = curated_tagline
    else:
        # Простой fallback — соберём из города + услуг
        services = card.get("services") or []
        first_two_services = [s.get("title", "") for s in services[:2] if s.get("title")]
        if first_two_services:
            tagline = f"Салон красоты в {city}: {', '.join(first_two_services).lower()}"
        else:
            tagline = f"Салон красоты в {city}" if city else "Салон красоты"

    meta = {
        "slug": bundle.slug,
        "brand": {
            "name": name,
            "shortName": short_name,
            "slug": bundle.slug,
            "tagline": tagline,
        },
        "name": name,
        "fullName": f"{name} — {tagline}" if tagline else name,
        "tagline": tagline,
        "city": city or "",
    }

    # Награда «Хорошее место» — серьёзный trust-signal, добавим в meta
    if card.get("good_place_year"):
        meta["awards"] = [{
            "type": "good_place",
            "year": card["good_place_year"],
            "label": f"Хорошее место {card['good_place_year']}",
        }]

    return meta


def _build_contacts(bundle: CardBundle, lead: Dict[str, Any]) -> Dict[str, Any]:
    """contacts block: phones, address, coordinates, hours."""
    card = bundle.card
    address = card.get("address") or {}
    phones = card.get("phones") or []
    main_phone = phones[0] if phones else (lead.get("phone") or "")

    contacts: Dict[str, Any] = {
        "phone": _format_phone(main_phone),
        "phoneRaw": _to_phone_raw(main_phone),
        "phones": [_format_phone(p) for p in phones],
        "whatsapp": _to_phone_raw(main_phone) if main_phone else "",
        "address": address.get("full") or (lead.get("address") or ""),
        "addressNote": address.get("additional") or None,
        "additionalAddresses": [],
        "workingHours": ((card.get("working_hours") or {}).get("text") or "").strip(),
        "hours": ((card.get("working_hours") or {}).get("text") or "").strip(),  # alias для шаблона
    }

    coords = card.get("coordinates")
    if isinstance(coords, dict) and coords.get("lat") and coords.get("lng"):
        contacts["coordinates"] = {"lat": float(coords["lat"]), "lng": float(coords["lng"])}

    # Чистим None
    return {k: v for k, v in contacts.items() if v not in (None, "")}


def _build_booking(bundle: CardBundle) -> Dict[str, Any]:
    """booking block: ссылка на онлайн-запись."""
    booking_url = ((bundle.card.get("urls") or {}).get("booking") or "").strip()
    return {"url": booking_url} if booking_url else {}


def _build_social(bundle: CardBundle, lead: Dict[str, Any]) -> List[Dict[str, str]]:
    """social block: VK, TG, IG из card.urls + lead."""
    urls = bundle.card.get("urls") or {}
    out: List[Dict[str, str]] = []
    name = bundle.card.get("title") or ""

    candidates = [
        ("vk", "ВКонтакте", "VK"),
        ("telegram", "Telegram", "TG"),
        ("instagram", "Instagram", "IG"),
        ("taplink", "TapLink", "TL"),
        ("whatsapp", "WhatsApp", "WA"),
    ]
    for key, label, short in candidates:
        href = (urls.get(key) or "").strip()
        if href:
            out.append({"href": href, "label": f"{label} {name}".strip(), "short": short})

    return out


def _build_sections(
    bundle: CardBundle, lead: Dict[str, Any], curated: Dict[str, Any]
) -> Dict[str, Any]:
    """Все sections.* за один проход."""
    card = bundle.card
    photo_map = bundle.photo_map
    slug = bundle.slug
    lead_id = bundle.lead_id

    # Hero
    hero_photo = get_hero_photo(photo_map, slug, lead_id, PUBLIC_DIR)
    hero_image = hero_photo["src"] if hero_photo else ""

    # Gallery
    gallery_items = build_block_photos(photo_map, "gallery", slug, lead_id, PUBLIC_DIR)

    # About
    about_items = build_block_photos(photo_map, "about", slug, lead_id, PUBLIC_DIR)

    # Team
    team_photos = build_block_photos(photo_map, "team", slug, lead_id, PUBLIC_DIR)
    # Team в шаблоне ожидает {name, role, image}. У нас name/role нет — только photo.
    # Для нативного v2: если photo_map дал team-фото с captioned_text — это и есть имя.
    # Пока: если фото есть, конструируем минимальный объект; если нет — блок выкл.
    team_items = []
    for p in team_photos:
        team_items.append({
            "name": p.get("description") or "Мастер",  # как fallback
            "role": "Мастер",
            "image": p["src"],
            "alt": p.get("alt") or "",
        })

    # Services — из card.services + photo_map.services
    services_items = _build_services_items(card, photo_map, slug, lead_id)

    # Reviews — из card.reviews_preview
    reviews_items = _build_reviews_items(card)

    # About text
    about_text = _build_about_text(card, curated)

    # FAQ
    faq_items = _build_faq_items(curated, card)

    sections = {
        "hero": _build_hero_section(card, lead, hero_image, hero_photo),
        "promotion": {"enabled": True},
        "services": {
            "enabled": bool(services_items),
            "items": services_items,
        },
        "gallery": {
            "enabled": len(gallery_items) >= 3,
            "title": "Наши работы",
            "subtitle": "Каждая деталь имеет значение",
            "items": gallery_items,
        },
        "team": {
            "enabled": bool(team_items),
            "items": team_items,
        },
        "reels": _build_reels(card),
        "reviews": {
            "enabled": bool(reviews_items),
            "items": reviews_items,
        },
        "about": {
            "enabled": bool(about_text),
            "showImages": bool(about_items),
            "text": about_text,
            "images": about_items,
        },
        "faq": {
            "enabled": bool(faq_items),
            "items": faq_items,
        },
        "bookingContacts": {
            "enabled": True,
            "showMap": True,
        },
    }

    # Price section отдельно — детальный прайс из чистых services
    price_section = _build_price_section(card)
    if price_section:
        sections["price"] = price_section

    return sections


def _build_hero_section(
    card: Dict[str, Any], lead: Dict[str, Any],
    hero_image: str, hero_photo: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """hero block."""
    name = card.get("title") or ""
    address = card.get("address") or {}
    city = address.get("locality") or ""
    cat = (lead.get("category") or "").lower()

    title_line1 = "Студия маникюра" if "nail" in cat else "Студия красоты"
    title_line1_small = "идеального маникюра" if "nail" in cat else "по созданию образа"

    return {
        "enabled": True,
        "image": hero_image,
        "imageAlt": (hero_photo or {}).get("alt") if hero_photo else "",
        "titleLine1": title_line1,
        "titleLine1Small": title_line1_small,
        "titleLine1SmallSize": "default",
        "titleLine2": _strip_brand_noise(name),
        "topLabel": f"{city} · запись онлайн" if city else "запись онлайн",
        "lead": _short_about_text(card),
        "ctaLabel": "Записаться",
    }


def _build_services_items(
    card: Dict[str, Any], photo_map: Dict[str, Any],
    slug: str, lead_id: int,
) -> List[Dict[str, Any]]:
    """
    services_items для блока Services. Сначала из card.services (чистые!),
    дополняем фото из photo_map.services если есть.
    """
    raw_services = card.get("services") or []
    photo_items = build_block_photos(photo_map, "services", slug, lead_id, PUBLIC_DIR)

    # Индекс фото по service_ref для матчинга
    photos_by_ref = {}
    for p in photo_items:
        ref = (p.get("serviceRef") or "").lower().strip()
        if ref and ref not in photos_by_ref:
            photos_by_ref[ref] = p["src"]

    items = []
    for svc in raw_services:
        title = (svc.get("title") or "").strip()
        if not title:
            continue
        description = (svc.get("description") or "").strip()
        price_text = (svc.get("price_text") or "").strip()
        if not price_text and svc.get("price"):
            price_text = f"{svc['price']} {svc.get('currency') or '₽'}".strip()

        # Фото услуги: 1) из card.services.photo_url напрямую (CDN URL),
        # 2) либо из photo_map.services по service_ref
        # На фронте мы используем локальные пути, но услуги Yandex CDN — можно оставить URL как есть,
        # шаблон поддержит прямой URL.
        photo_src = svc.get("photo_url") or photos_by_ref.get(_guess_service_ref(title))

        items.append({
            "title": title,
            "short": description[:60] if description else None,
            "description": description,
            "priceFrom": price_text or "по запросу",
            "image": photo_src or None,
        })

    # Очистка None
    items = [{k: v for k, v in i.items() if v is not None} for i in items]
    return items[:12]  # шаблон обычно показывает 5-9


def _guess_service_ref(title: str) -> str:
    """Грубое отображение русского названия услуги в service_ref enum."""
    t = title.lower()
    if "маникюр" in t: return "manicure"
    if "педикюр" in t: return "pedicure"
    if "окрашивание" in t or "колорист" in t: return "coloring"
    if "стрижк" in t: return "haircut"
    if "ресниц" in t: return "lashes"
    if "бров" in t: return "brows"
    if "макияж" in t: return "makeup"
    return "other"


def _build_reviews_items(card: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Reviews из card.reviews_preview."""
    raw = card.get("reviews_preview") or []
    items = []
    for r in raw:
        text = (r.get("text") or "").strip()
        author = (r.get("author") or "").strip()
        if not text or not author or len(text) < 20:
            continue
        item = {
            "author": author,
            "text": text,
            "rating": r.get("rating"),
            "date": r.get("date") or "",
        }
        if r.get("business_reply"):
            item["businessReply"] = r["business_reply"]
        items.append(item)
    return items[:6]


def _build_about_text(card: Dict[str, Any], curated: Dict[str, Any]) -> str:
    """About text: curated > yandex-derived."""
    curated_text = (curated.get("about") or "").strip()
    if curated_text:
        return curated_text

    # Fallback: соберём из данных card
    name = card.get("title") or "Салон"
    address = card.get("address") or {}
    rating = card.get("rating")
    review_count = card.get("review_count")
    parts = [f"{name} в городе {address.get('locality')}." if address.get("locality") else f"{name}."]
    if rating and review_count:
        parts.append(f"Рейтинг {rating} из 5 на основе {review_count} отзывов.")
    if card.get("good_place_year"):
        parts.append(f"Награда «Хорошее место {card['good_place_year']}».")
    return " ".join(parts)


def _build_faq_items(curated: Dict[str, Any], card: Dict[str, Any]) -> List[Dict[str, str]]:
    """FAQ из curated, иначе сгенерим базовый."""
    curated_faq = curated.get("faq") or []
    if curated_faq:
        items = [{"q": x.get("q", "").strip(), "a": x.get("a", "").strip()}
                 for x in curated_faq if x.get("q") and x.get("a")]
        if items:
            return items[:8]

    # Fallback: 2-3 стандартных вопроса из данных
    address = card.get("address") or {}
    phones = card.get("phones") or []
    items = []
    if address.get("full"):
        items.append({"q": "Где находится салон?", "a": f"Салон находится по адресу: {address['full']}."})
    if phones:
        items.append({"q": "Как записаться?", "a": f"Позвоните по телефону {phones[0]} или оставьте заявку на сайте."})
    services = card.get("services") or []
    if services:
        s_titles = [s.get("title") for s in services[:3] if s.get("title")]
        if s_titles:
            items.append({"q": "Какие услуги вы предоставляете?", "a": f"Среди наших основных услуг: {', '.join(s_titles)} и многое другое."})
    return items


def _build_reels(card: Dict[str, Any]) -> Dict[str, Any]:
    """Reels из card.videos. Шаблон может уметь — пока выключим, оставим items."""
    videos = card.get("videos") or []
    items = []
    for v in videos[:9]:
        items.append({
            "id": v.get("id"),
            "thumbnail": v.get("thumbnail_url"),
            "video": v.get("video_url"),
            "width": v.get("width"),
            "height": v.get("height"),
        })
    return {
        "enabled": False,  # включим вручную если шаблон поддержит
        "items": items,
    }


def _build_price_section(card: Dict[str, Any]) -> Dict[str, Any] | None:
    """Price из чистых card.services. Без is_junk-фильтров — данные уже чистые."""
    services = card.get("services") or []
    if not services:
        return None
    items = []
    for s in services:
        title = (s.get("title") or "").strip()
        if not title or len(title) < 3:
            continue
        price_text = (s.get("price_text") or "").strip()
        if not price_text and s.get("price"):
            price_text = f"{s['price']} ₽"
        items.append({
            "title": title,
            "price": price_text or "по запросу",
            "priceFrom": False,
            "duration_min": None,
            "description": (s.get("description") or "").strip()[:200] or None,
        })
    if not items:
        return None
    return {
        "enabled": True,
        "title": "Прайс",
        "subtitle": None,
        "note": "Окончательная стоимость уточняется при записи.",
        "groups": [{"title": None, "items": items}],
    }


def _build_section_order(sections: Dict[str, Any]) -> List[str]:
    """Финальный sectionsOrder из включённых блоков."""
    order = list(DEFAULT_SECTION_ORDER)
    if sections.get("price", {}).get("enabled"):
        # Вставим price после services, или перед gallery
        if "services" in order:
            order.insert(order.index("services") + 1, "price")
        else:
            order.insert(order.index("gallery") if "gallery" in order else 0, "price")
    # Удаляем выключенные
    return [s for s in order if sections.get(s, {}).get("enabled", False) or s in ("hero", "bookingContacts", "promotion")]


def _build_block_flags(sections: Dict[str, Any]) -> Dict[str, bool]:
    """block_flags — простой словарь enabled-флагов."""
    return {
        "hero": sections.get("hero", {}).get("enabled", False),
        "gallery": sections.get("gallery", {}).get("enabled", False),
        "team": sections.get("team", {}).get("enabled", False),
        "services": sections.get("services", {}).get("enabled", False),
        "faq": sections.get("faq", {}).get("enabled", False),
        "reviews": sections.get("reviews", {}).get("enabled", False),
        "about": sections.get("about", {}).get("enabled", False),
        "contacts": True,
        "promotions": True,
        "price": sections.get("price", {}).get("enabled", False),
    }


# ======================================================================
# Helpers
# ======================================================================

def _strip_brand_noise(name: str) -> str:
    noise = {"студия", "красоты", "салон", "beauty", "studio", "center", "центр", "spa", "спа"}
    words = (name or "").strip().split()
    filtered = [w for w in words if w.lower() not in noise]
    return " ".join(filtered).strip() or (name or "").strip()


def _format_phone(phone: str) -> str:
    """+7 (XXX) XXX-XX-XX."""
    if not phone: return ""
    digits = re.sub(r"[^\d]", "", phone)
    if digits.startswith("8"): digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits[0]} ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return phone


def _to_phone_raw(phone: str) -> str:
    return re.sub(r"[^\d+]", "", phone or "")


def _short_about_text(card: Dict[str, Any]) -> str:
    """Короткий текст для hero.lead — первое предложение из about или сгенерим."""
    name = card.get("title") or "Салон"
    address = card.get("address") or {}
    locality = address.get("locality") or ""
    street_house = " ".join([
        address.get("street") or "",
        address.get("house") or "",
    ]).strip()
    if locality and street_house:
        return f"{name} в {locality}, {street_house}."
    if locality:
        return f"{name} в {locality}."
    return name
```

---

## Part D — `config_builder/io.py` расширение

Добавить функции для v2:

```python
def list_image_urls_v2(slug: str, lead_id: int, folder: str) -> List[str]:
    """Web-relative URLs для public/{slug}-{lead_id}/{folder}/. Возвращает /{slug}/{folder}/file.jpg
    (без -lead_id, как ждёт шаблон)."""
    root = RADAR_PUBLIC_DIR / f"{slug}-{lead_id}" / folder
    if not root.exists() or not root.is_dir():
        return []
    files = []
    for p in sorted(root.iterdir()):
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            files.append(f"/{slug}/{folder}/{p.name}")
    return files


def copy_public_assets_v2(slug: str, lead_id: int, no_copy: bool) -> int:
    """
    Копирует public/{slug}-{lead_id}/* в neuralsync/public/{slug}/* (БЕЗ -{lead_id}).
    Шаблон ждёт пути /{slug}/folder/file.jpg, поэтому переименовываем при копировании.
    """
    if no_copy:
        return 0
    src = RADAR_PUBLIC_DIR / f"{slug}-{lead_id}"
    dst = NEURALSYNC_PUBLIC_DIR / slug
    if not src.exists() or not src.is_dir():
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return sum(1 for p in dst.rglob("*") if p.is_file())
```

---

## Part E — `config_builder/cli.py` расширение

Новый флаг `--v2` (по умолчанию: автоопределение).

```python
import argparse
import sys
from pathlib import Path

from config import settings
from utils import slugify_name

from config_builder.builder import build_config       # legacy
from config_builder.builder_v2 import build_config_v2  # NEW
from config_builder.io import copy_public_assets, copy_public_assets_v2, load_lead, log
from config_builder.js_export import to_js_module, validate_js_with_node


def _has_card_v2(slug: str, lead_id: int) -> bool:
    """Проверка наличия card.json для лида."""
    return (settings.data_dir / "yandex" / f"{slug}-{lead_id}" / "card.json").exists()


def run_build(lead_id: int, dry_run: bool = False, no_copy: bool = False, force_legacy: bool = False) -> int:
    """
    Сборка с автоопределением v1/v2:
    - если есть card.json и не --legacy → v2
    - иначе → legacy v1
    """
    started = time.perf_counter()
    log(f"🎯 Старт сборки lead_id={lead_id}")

    lead = load_lead(lead_id)
    if not lead:
        log(f"❌ Лид ID={lead_id} не найден")
        return 1

    slug = slugify_name(str(lead.get("name") or ""), lead_id)
    use_v2 = (not force_legacy) and _has_card_v2(slug, lead_id)
    log(f"🔧 Pipeline: {'v2 (native)' if use_v2 else 'v1 (legacy)'}")

    try:
        if use_v2:
            config = build_config_v2(lead_id)
        else:
            config = build_config(lead_id)
    except Exception as exc:
        log(f"❌ Ошибка сборки конфига: {exc}")
        return 1

    if dry_run:
        log("🧪 Dry-run — конфиг НЕ записан")
        print(json.dumps(config, ensure_ascii=False, indent=2))
        return 0

    # Запись и валидация
    NEURALSYNC_CONFIGS_DIR = Path(settings.neuralsync_root) / "src" / "configs"
    NEURALSYNC_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    config_path = NEURALSYNC_CONFIGS_DIR / f"{slug}-{lead_id}.config.js"
    js_module = to_js_module(config, slug)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(js_module)
    validate_js_with_node(config_path)
    log(f"📝 Записан {config_path}")

    # Копирование фото — разные функции для v1/v2
    if use_v2:
        copied = copy_public_assets_v2(slug, lead_id, no_copy=no_copy)
    else:
        copied = copy_public_assets(slug, no_copy=no_copy)
    if not no_copy:
        log(f"📂 Скопировано {copied} файлов в neuralsync/public/{slug}/")

    log(f"✅ Готово, время {time.perf_counter() - started:.1f} сек")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Config Builder for neuralsync")
    parser.add_argument("lead_id", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-copy", action="store_true")
    parser.add_argument("--legacy", action="store_true",
                        help="Принудительно использовать старый pipeline v1, даже если есть card.json")
    args = parser.parse_args()
    sys.exit(run_build(args.lead_id, dry_run=args.dry_run, no_copy=args.no_copy, force_legacy=args.legacy))
```

---

## Part F — Расширение шаблона neuralsync (минимальное, опциональное)

Эти расширения **не блокируют** Этап 6, но делают результат лучше. Можно сделать в Stage 6.5 после визуального аудита.

### F.1 Hero — отображение `imageAlt` и `awards`

В `Hero.jsx`:
- Использовать `heroConfig.imageAlt` для `<img alt={...}>`
- Если `config.meta.awards` содержит `good_place` — показать badge у заголовка

### F.2 Reviews — отображение `businessReply`

В `Reviews.jsx`:
- Если `r.businessReply` есть — показать ниже текста отзыва, с маркером «Ответ владельца:»

### F.3 Team — унификация чтения

В `Team.jsx` сейчас `config.team` (массив на верхнем уровне). Изменить на `config.sections?.team?.items`.

### F.4 Services — отображение `image`

В `Services.jsx`:
- Если `service.image` есть — показать миниатюру в карточке

Это **отдельная задача шаблона**, не входит в обязательный DoD этого ТЗ.

---

## DoD

- [ ] Создан `enrichment/card_loader.py` с `load_card_bundle()` и `load_curated_optional()`
- [ ] Создан `config_builder/photo_renderer.py` с `build_block_photos()` и `get_hero_photo()`
- [ ] Создан `config_builder/builder_v2.py` с `build_config_v2()`
- [ ] В `config_builder/io.py` добавлены `list_image_urls_v2()` и `copy_public_assets_v2()`
- [ ] В `config_builder/cli.py` логика автоопределения v1/v2 + флаг `--legacy`
- [ ] **Тест на mood-26:**
  - [ ] `python -m config_builder.cli 26` отрабатывает (использует v2 автоматически)
  - [ ] Сгенерирован `neuralsync/src/configs/mood-26.config.js`
  - [ ] Скопированы файлы в `neuralsync/public/mood/`
  - [ ] **Файл проходит `node --check`** (валидация JS-синтаксиса)
- [ ] **Тест на алёна-16:**
  - [ ] `python -m config_builder.cli 16` отрабатывает
  - [ ] Сгенерирован конфиг
- [ ] **Тест fallback:** на лиде где card.json нет, `python -m config_builder.cli {id}` использует legacy v1
- [ ] **Тест dry-run:** `python -m config_builder.cli 26 --dry-run` показывает JSON без записи

---

## Test commands

```powershell
cd "D:\1 KURSOR_PROJ\11 PARSER"

# 1. Сборка для mood-26 (есть card.json → v2 автоматически)
python -m config_builder.cli 26

# 2. Проверить что файл создан и корректный
type "D:\2 Clode Proj\1\neuralsync\src\configs\mood-26.config.js" | Select-Object -First 30

# 3. Запустить сайт локально
cd "D:\2 Clode Proj\1\neuralsync"
npm run dev
# Открыть http://localhost:5173/?lead=mood-26 (или как роутер устроен)

# 4. Сборка для второго лида
cd "D:\1 KURSOR_PROJ\11 PARSER"
python -m config_builder.cli 16

# 5. Принудительно legacy (для регрессии)
python -m config_builder.cli 26 --legacy

# 6. Dry-run — посмотреть JSON
python -m config_builder.cli 26 --dry-run
```

---

## Что НЕ в этом этапе

- Расширения шаблона (Hero awards, Reviews businessReply, Team unification, Services image) — Stage 6.5
- Удаление старого `builder.py` — оставляем как fallback
- Обновление дашборда чтобы показывал v1/v2 и qualification — отдельная задача
- Recovery от пустых данных (например, qualification.qualified=False) — пока сборка проходит, шаблон показывает что есть
- TapLink/VK-парсеры — отдельные этапы

---

## Notes for Codex

- ASCII-only имена файлов и папок
- Russian text в JSON: `ensure_ascii=False`
- Import порядок: stdlib → third-party → local
- Type hints обязательны для public-функций
- Логирование: `from config_builder.io import log`
- `slugify_name` — из `utils`, существует
- `detect_city` — из `utils`, существует
- Шаблон `_default.config.js` в neuralsync можно посмотреть для справки по полям
- Если поле card.json пустое — fallback на разумный default, не ронять билд
- Качество > скорость. Каждое поле должно работать на улучшение лендинга, не на простое переливание данных
