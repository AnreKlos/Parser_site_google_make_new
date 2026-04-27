#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "leads.db"
CURATED_DIR = BASE_DIR / "data" / "curated"
YANDEX_DIR = BASE_DIR / "data" / "yandex"
EXTRACTED_DIR = BASE_DIR / "data" / "extracted"
RADAR_PUBLIC_DIR = BASE_DIR / "public"

NEURALSYNC_ROOT = Path(r"D:\2 Clode Proj\1\neuralsync")
NEURALSYNC_CONFIGS_DIR = NEURALSYNC_ROOT / "src" / "configs"
NEURALSYNC_PUBLIC_DIR = NEURALSYNC_ROOT / "public"

DEFAULT_TOKENS = {
    "GOLD": "#C9A87A",
    "GOLD_DIM": "#A68B5A",
    "GOLD_BRIGHT": "#D4B88A",
    "TEXT": "#F0EBE3",
    "TEXT_SOFT": "#B5AFA7",
    "MUTED": "#9A938B",
    "BG": "#0E0C0B",
    "CHOCOLATE": "#151210",
    "SURFACE": "#1A1714",
    "SURFACE_L": "#262220",
    "BORDER": "rgba(255,255,255,0.06)",
    "BORDER_H": "rgba(255,255,255,0.14)",
    "EASE": [0.16, 1, 0.3, 1],
}

DEFAULT_LEGAL = {
    "showInFooter": True,
    "placeholder": "Реквизиты предоставим при заключении договора",
}

DEFAULT_CONTENT = {
    "promotion": {
        "title": "Особое предложение для новых клиентов",
        "text": "Оставьте заявку — администратор подберет подходящую услугу и удобное время визита.",
    }
}

DEFAULT_CHAT_WIDGET = {
    "enabled": True,
    "tooltipDelayMs": 8000,
    "mountDelayMs": 3000,
    "greeting": "Здравствуйте! Я цифровой консьерж {{brandName}}. Чем могу помочь?",
}

DEFAULT_SECTION_ORDER = [
    "hero",
    "promotion",
    "services",
    "gallery",
    "team",
    "reviews",
    "about",
    "faq",
    "bookingContacts",
]


def log(message: str) -> None:
    print(message, flush=True)


def slugify_name(name: str, lead_id: int) -> str:
    translit_map = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
        "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    lower = (name or "").strip().lower()
    translit = "".join(translit_map.get(ch, ch) for ch in lower)
    translit = re.sub(r"[^a-z0-9]+", "-", translit)
    translit = re.sub(r"-+", "-", translit).strip("-")
    return translit or f"lead-{lead_id}"


def slug_to_var_name(slug: str) -> str:
    parts = [p for p in re.split(r"[^a-zA-Z0-9]+", slug) if p]
    if not parts:
        return "leadConfig"
    first = parts[0].lower()
    rest = [p[:1].upper() + p[1:] for p in parts[1:]]
    return f"{first}{''.join(rest)}Config"


def detect_city(address: str) -> str:
    if not address:
        return ""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    street_markers = (
        "ул", "улица", "пр-т", "проспект", "пер", "переулок", "шоссе", "б-р", "бул", "наб", "дом", "д.",
    )
    skip_words = {"россия", "russia", "российская федерация"}
    for part in parts:
        cleaned = re.sub(r"^г\.?\s*", "", part, flags=re.IGNORECASE).strip()
        low = cleaned.lower()
        if not cleaned or low in skip_words:
            continue
        if any(low.startswith(m) for m in street_markers):
            continue
        if "обл" in low or "район" in low or "округ" in low or "край" in low:
            continue
        if re.search(r"\d", low):
            continue
        return cleaned
    return ""


def load_lead(lead_id: int) -> Optional[Dict[str, Any]]:
    if not DB_PATH.exists():
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return dict(row) if row else None


def read_json_if_exists(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else None


def list_image_urls(slug: str, folder: str) -> List[str]:
    root = RADAR_PUBLIC_DIR / slug / folder
    if not root.exists() or not root.is_dir():
        return []
    files = []
    for p in sorted(root.iterdir()):
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            files.append(f"/{slug}/{folder}/{p.name}")
    return files


def short_about_text(text: str, fallback: str) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip())
    if not value:
        return fallback
    parts = re.split(r"(?<=[.!?])\s+", value)
    candidate = parts[0].strip() if parts else value
    return candidate if len(candidate) <= 220 else candidate[:217].rstrip() + "..."


def to_phone_raw(phone: str) -> str:
    return re.sub(r"[^\d+]", "", phone or "")


def pick_social_links(lead: Dict[str, Any]) -> List[Dict[str, str]]:
    out = []
    social_candidates = [
        ("vk_url", "ВКонтакте", "VK"),
        ("instagram_url", "Instagram", "IG"),
        ("telegram_url", "Telegram", "TG"),
    ]
    for key, label, short in social_candidates:
        url = str(lead.get(key) or "").strip()
        if url:
            out.append({"href": url, "label": f"{label} {lead.get('name') or ''}".strip(), "short": short})

    social_raw = lead.get("social_links")
    if social_raw and isinstance(social_raw, str):
        try:
            parsed = json.loads(social_raw)
            if isinstance(parsed, dict):
                for label, short, key in (("ВКонтакте", "VK", "vk"), ("Telegram", "TG", "telegram"), ("Instagram", "IG", "instagram")):
                    urls = parsed.get(key) or []
                    if isinstance(urls, list):
                        for href in urls:
                            href = str(href or "").strip()
                            if href and not any(item["href"] == href for item in out):
                                out.append({"href": href, "label": f"{label} {lead.get('name') or ''}".strip(), "short": short})
        except Exception:
            pass
    return out


def normalize_service_title(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\sа-яё]", " ", (value or "").lower())).strip()


def merge_services(curated_services: List[Dict[str, Any]], yandex_services: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    yandex_pool = []
    for item in yandex_services:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        price = str(item.get("price") or "").strip()
        if not name:
            continue
        yandex_pool.append({"name": name, "price": price, "norm": normalize_service_title(name)})

    used_yandex = set()

    for item in curated_services:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        short = str(item.get("short") or "").strip()
        description = str(item.get("description") or "").strip()
        price_from = str(item.get("priceFrom") or "").strip()

        norm_title = normalize_service_title(title)
        best_idx = None
        best_score = 0.0
        for idx, y_item in enumerate(yandex_pool):
            yn = y_item["norm"]
            if not yn:
                continue
            score = 0.0
            if norm_title in yn or yn in norm_title:
                score = 0.9
            else:
                tokens_a = set(norm_title.split())
                tokens_b = set(yn.split())
                if tokens_a and tokens_b:
                    score = len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is not None and best_score >= 0.5:
            y_item = yandex_pool[best_idx]
            used_yandex.add(best_idx)
            if y_item.get("price"):
                price_from = y_item["price"]

        merged.append(
            {
                "title": title,
                "short": short or title,
                "description": description or short or title,
                "priceFrom": price_from or "по запросу",
            }
        )

    for idx, y_item in enumerate(yandex_pool):
        if idx in used_yandex:
            continue
        merged.append(
            {
                "title": y_item["name"],
                "short": y_item["name"],
                "description": y_item["name"],
                "priceFrom": y_item.get("price") or "по запросу",
            }
        )

    return merged[:20]


def normalize_extracted_services(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("name") or item.get("title") or "").strip()
        if not title:
            continue
        description = str(item.get("description") or "").strip()
        price = str(item.get("price") or "").strip()
        image = str(item.get("image") or item.get("image_url") or "").strip()
        out.append(
            {
                "title": title,
                "short": title,
                "description": description or title,
                "priceFrom": price or "по запросу",
                "image": image,
            }
        )
    return out[:20]


def normalize_extracted_faq(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        q = str(item.get("q") or "").strip()
        a = str(item.get("a") or "").strip()
        if q and a:
            out.append({"q": q, "a": a})
    return out[:20]


def prettify_name_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    parts = [p for p in re.split(r"[_\-\s]+", stem) if p]
    if not parts:
        return "Мастер"
    words = [p.capitalize() for p in parts[:2]]
    return " ".join(words)


def build_config(lead_id: int) -> Dict[str, Any]:
    lead = load_lead(lead_id)
    if not lead:
        raise RuntimeError(f"Лид ID={lead_id} не найден")

    lead_name = str(lead.get("name") or f"Lead {lead_id}")
    slug = slugify_name(lead_name, lead_id)

    curated_path = CURATED_DIR / f"{slug}.json"
    yandex_path = YANDEX_DIR / f"{slug}.json"
    extracted_path = EXTRACTED_DIR / f"{slug}.json"

    log(f"📖 Читаю curated/{slug}.json")
    curated = read_json_if_exists(curated_path) or {}

    log(f"📖 Читаю yandex/{slug}.json")
    yandex_payload = read_json_if_exists(yandex_path) or {}
    yandex = yandex_payload.get("yandex") if isinstance(yandex_payload.get("yandex"), dict) else {}

    log(f"📖 Читаю extracted/{slug}.json")
    extracted_payload = read_json_if_exists(extracted_path) or {}

    hero_images = list_image_urls(slug, "hero")
    gallery_images = list_image_urls(slug, "gallery")
    about_images = list_image_urls(slug, "about")
    team_images = list_image_urls(slug, "team")
    log(f"📖 Читаю public/{slug}/ ({len(hero_images)} фото в hero, {len(gallery_images)} в gallery, {len(team_images)} в team)")

    curated_meta = curated.get("meta") if isinstance(curated.get("meta"), dict) else {}
    curated_tagline = str(curated_meta.get("tagline") or "").strip()
    curated_about = str(curated.get("about") or "").strip()
    curated_reviews = curated.get("reviews") if isinstance(curated.get("reviews"), list) else []
    curated_faq = curated.get("faq") if isinstance(curated.get("faq"), list) else []
    curated_services = curated.get("services") if isinstance(curated.get("services"), list) else []

    extracted_services_raw = extracted_payload.get("serviceCarousel") if isinstance(extracted_payload.get("serviceCarousel"), list) else []
    if not extracted_services_raw and isinstance(extracted_payload.get("services_carousel"), list):
        extracted_services_raw = extracted_payload.get("services_carousel")
    extracted_faq_raw = extracted_payload.get("faq_accordion") if isinstance(extracted_payload.get("faq_accordion"), list) else []
    extracted_services = normalize_extracted_services(extracted_services_raw)
    extracted_faq = normalize_extracted_faq(extracted_faq_raw)

    yandex_address = str(yandex.get("address") or "").strip()
    lead_address = str(lead.get("address") or "").strip()
    city = detect_city(lead_address) or str(yandex_payload.get("city") or "").strip()
    address = yandex_address or lead_address

    y_phones = yandex.get("phones") if isinstance(yandex.get("phones"), list) else []
    phones = [str(p).strip() for p in y_phones if str(p).strip()]
    fallback_phone = str(lead.get("phone") or "").strip()
    if not phones and fallback_phone:
        phones = [fallback_phone]
    phone_main = phones[0] if phones else ""
    phone_raw = to_phone_raw(phone_main)

    coords = yandex.get("coordinates") if isinstance(yandex.get("coordinates"), dict) else None
    additional_addresses = yandex.get("additional_addresses") if isinstance(yandex.get("additional_addresses"), list) else []

    booking_url = str(lead.get("yclients_url") or "").strip()
    social_links = pick_social_links(lead)
    vk_url = str(lead.get("vk_url") or "").strip()
    if not vk_url:
        for item in social_links:
            if item.get("short") == "VK":
                vk_url = item.get("href") or ""
                break

    fallback_title_line1 = "Студия красоты" if str(lead.get("category") or "").strip() in {"", "other"} else str(lead.get("category") or "").strip()

    merged_services = merge_services(curated_services, yandex.get("services") if isinstance(yandex.get("services"), list) else [])
    effective_faq = extracted_faq if extracted_faq else curated_faq

    team_items = [{"name": prettify_name_from_filename(Path(url).name), "photo": url} for url in team_images]

    reels_enabled = False
    sections_order = DEFAULT_SECTION_ORDER.copy()
    if reels_enabled:
        sections_order = ["hero", "promotion", "reels", "services", "gallery", "team", "reviews", "about", "faq", "bookingContacts"]
    if extracted_services:
        sections_order = [item for item in sections_order if item != "serviceCarousel"]
        hero_index = sections_order.index("hero") if "hero" in sections_order else -1
        insert_at = hero_index + 1 if hero_index >= 0 else 0
        sections_order.insert(insert_at, "serviceCarousel")

    hero_image = hero_images[0] if hero_images else (gallery_images[0] if gallery_images else "")

    log("🛠 Собираю секции")

    config = {
        "meta": {
            "slug": slug,
            "brand": {
                "name": lead_name,
                "slug": slug,
                "tagline": curated_tagline or fallback_title_line1,
            },
            "name": lead_name,
            "fullName": f"{lead_name} — {curated_tagline or fallback_title_line1}",
            "tagline": curated_tagline or fallback_title_line1,
            "city": city,
        },
        "contacts": {
            "phone": phone_main,
            "phoneRaw": phone_raw,
            "phones": phones,
            "whatsapp": phone_raw if phone_raw else "",
            "address": address,
            "additionalAddresses": additional_addresses,
            "workingHours": str(yandex.get("working_hours") or "").strip() or "ежедневно 10:00–20:00",
            "vk": vk_url,
        },
        "booking": {},
        "social": social_links,
        "tokens": DEFAULT_TOKENS,
        "legal": DEFAULT_LEGAL,
        "content": DEFAULT_CONTENT,
        "sectionsOrder": sections_order,
        "copyrightYear": datetime.now().year,
        "sections": {
            "hero": {
                "enabled": True,
                "image": hero_image,
                "titleLine1": curated_tagline or fallback_title_line1,
                "titleLine2": lead_name,
                "topLabel": "Премиум студия красоты",
                "lead": short_about_text(curated_about, "Подчеркнем вашу индивидуальность и соберем образ под событие и настроение."),
            },
            "promotion": {"enabled": True},
            "serviceCarousel": {
                "enabled": bool(extracted_services),
                "items": extracted_services,
            },
            "services": {
                "enabled": bool(merged_services),
                "items": merged_services,
            },
            "gallery": {
                "enabled": len(gallery_images) >= 3,
                "title": "Наши работы",
                "subtitle": "Каждая деталь имеет значение",
                "items": gallery_images,
            },
            "team": {
                "enabled": bool(team_items),
                "items": team_items,
            },
            "reels": {
                "enabled": False,
                "items": [],
            },
            "reviews": {
                "enabled": bool(curated_reviews),
                "items": curated_reviews,
            },
            "about": {
                "enabled": bool(curated_about),
                "showImages": bool(about_images),
                "text": curated_about,
                "images": about_images,
            },
            "faq": {
                "enabled": bool(effective_faq),
                "items": effective_faq,
            },
            "bookingContacts": {
                "enabled": True,
                "showMap": True,
            },
        },
        "features": {
            "chatWidget": DEFAULT_CHAT_WIDGET,
        },
    }

    if coords:
        config["contacts"]["coordinates"] = coords

    if booking_url:
        config["booking"]["url"] = booking_url

    return config


def js_escape_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace("\r", "\\r").replace("\n", "\\n")
    return f"'{escaped}'"


def to_js_literal(value: Any, indent: int = 0) -> str:
    space = "  " * indent
    next_space = "  " * (indent + 1)

    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return js_escape_string(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        parts = [to_js_literal(v, indent + 1) for v in value]
        return "[\n" + ",\n".join(f"{next_space}{p}" for p in parts) + f"\n{space}]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = []
        for key, val in value.items():
            js_key = key if re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", key) else js_escape_string(key)
            lines.append(f"{next_space}{js_key}: {to_js_literal(val, indent + 1)}")
        return "{\n" + ",\n".join(lines) + f"\n{space}}}"
    return js_escape_string(str(value))


def to_js_module(config: Dict[str, Any], slug: str) -> str:
    var_name = slug_to_var_name(slug)
    body = to_js_literal(config, indent=0)
    return f"export const {var_name} = {body};\n\nexport default {var_name};\n"


def validate_js_with_node(path: Path) -> None:
    result = subprocess.run(
        ["node", "--check", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        raise RuntimeError(f"node --check failed for {path}:\n{output}")


def copy_public_assets(slug: str, no_copy: bool) -> int:
    if no_copy:
        return 0
    src = RADAR_PUBLIC_DIR / slug
    dst = NEURALSYNC_PUBLIC_DIR / slug
    if not src.exists() or not src.is_dir():
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return sum(1 for p in dst.rglob("*") if p.is_file())


def run_build(lead_id: int, dry_run: bool, no_copy: bool) -> int:
    started = time.perf_counter()
    log(f"🎯 Старт сборки lead_id={lead_id}")

    lead = load_lead(lead_id)
    if not lead:
        log(f"❌ Лид ID={lead_id} не найден")
        return 1

    slug = slugify_name(str(lead.get("name") or ""), lead_id)
    config = build_config(lead_id)

    if dry_run:
        log("🧪 Dry-run: показываю собранный конфиг (без записи)")
        print(json.dumps(config, ensure_ascii=False, indent=2))
        elapsed = time.perf_counter() - started
        log(f"✅ Готово, время {elapsed:.1f} сек")
        return 0

    NEURALSYNC_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    config_path = NEURALSYNC_CONFIGS_DIR / f"{slug}.config.js"

    js_module = to_js_module(config, slug)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(js_module)

    validate_js_with_node(config_path)
    log("✓ Валидация JS пройдена")
    log(f"📝 Записан {config_path}")

    copied_files = copy_public_assets(slug, no_copy=no_copy)
    if not no_copy:
        log(f"📂 Скопировано {copied_files} фото в {NEURALSYNC_PUBLIC_DIR / slug}")

    elapsed = time.perf_counter() - started
    log(f"✅ Готово, время {elapsed:.1f} сек")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Config Builder for neuralsync")
    parser.add_argument("lead_id", type=int, help="Lead ID")
    parser.add_argument("--dry-run", action="store_true", help="Собрать и показать конфиг без записи файлов")
    parser.add_argument("--no-copy", action="store_true", help="Не копировать папку public/<slug>")
    args = parser.parse_args()

    try:
        code = run_build(args.lead_id, dry_run=args.dry_run, no_copy=args.no_copy)
    except Exception as exc:
        log(f"❌ Ошибка: {exc}")
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
