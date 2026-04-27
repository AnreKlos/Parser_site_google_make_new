#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from sqlalchemy import select

from db.database import get_async_session
from db.models import Lead

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "openai/gpt-4o-mini"
REQUEST_TIMEOUT = 35
MAX_JSON_RETRIES = 2
OUTPUT_DIR = Path(__file__).parent / "data" / "curated"
NEURALSYNC_ENV_PATH = Path(r"D:\2 Clode Proj\1\neuralsync\.env")

FALLBACK_AUTHORS = ["Мария", "Анна", "Ольга", "Елена", "Наталья", "Татьяна"]


def safe_print(text: str) -> None:
    try:
        print(text, flush=True)
    except (UnicodeEncodeError, UnicodeError):
        print(text.encode("cp1251", errors="replace").decode("cp1251"), flush=True)


def init_env() -> None:
    load_dotenv()
    if not os.getenv("OPENROUTER_API_KEY") and NEURALSYNC_ENV_PATH.exists():
        load_dotenv(NEURALSYNC_ENV_PATH)


def extract_json_from_text(text: str) -> Optional[Any]:
    if text is None:
        return None
    candidate = text.strip()
    if not candidate:
        return None

    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        pass

    md_match = re.search(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL | re.IGNORECASE)
    if md_match:
        try:
            return json.loads(md_match.group(1).strip())
        except (json.JSONDecodeError, TypeError):
            pass

    obj_match = re.search(r"\{[\s\S]*\}", candidate)
    if obj_match:
        try:
            return json.loads(obj_match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass

    arr_match = re.search(r"\[[\s\S]*\]", candidate)
    if arr_match:
        try:
            return json.loads(arr_match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


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


def detect_city(address: Optional[str]) -> str:
    if not address:
        return "вашем городе"
    parts = [p.strip() for p in str(address).split(",") if p.strip()]
    if not parts:
        return "вашем городе"
    first = re.sub(r"^г\.?\s*", "", parts[0], flags=re.IGNORECASE).strip()
    if first and not re.match(r"^\d", first):
        return first
    return "вашем городе"


def extract_about_source(audit_notes: Optional[str]) -> str:
    notes = audit_notes or ""
    patterns = [
        r"(?:о\s*нас|about)\s*[:\-]\s*(.{40,900})",
        r"(?:описание|description)\s*[:\-]\s*(.{40,900})",
    ]
    for pattern in patterns:
        match = re.search(pattern, notes, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return compact_text(match.group(1))
    return ""


def extract_tagline_source(audit_notes: Optional[str]) -> str:
    notes = audit_notes or ""
    patterns = [
        r"(?:слоган|tagline)\s*[:\-]\s*(.{5,120})",
        r"(?:девиз|motto)\s*[:\-]\s*(.{5,120})",
    ]
    for pattern in patterns:
        match = re.search(pattern, notes, flags=re.IGNORECASE)
        if match:
            return compact_text(match.group(1))
    return ""


def parse_raw_reviews(raw_reviews: Optional[str]) -> List[Dict[str, Any]]:
    if not raw_reviews:
        return []
    try:
        parsed = json.loads(raw_reviews)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []

    out: List[Dict[str, Any]] = []
    for i, review in enumerate(parsed):
        if not isinstance(review, dict):
            continue
        text = compact_text(str(review.get("text") or review.get("review") or review.get("content") or ""))
        if not text or len(text) < 8:
            continue
        author = compact_text(str(review.get("author") or review.get("author_name") or review.get("name") or ""))
        out.append({"index": i, "author": author, "text": text, "rating": review.get("rating")})
    return out


def fallback_author_name(raw_author: Optional[str], index: int) -> str:
    author = compact_text(raw_author or "")
    if author and re.search(r"[А-Яа-я]", author):
        first = re.sub(r"[^А-Яа-яA-Za-z]", "", author.split()[0])
        if first:
            return first.capitalize()
    return FALLBACK_AUTHORS[index % len(FALLBACK_AUTHORS)]


def call_openrouter_json(system_prompt: str, user_prompt: str) -> Optional[Any]:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY не найден в окружении (.env)")

    total_attempts = MAX_JSON_RETRIES + 1
    payload = {
        "model": OPENROUTER_MODEL,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8501",
        "X-Title": "KURSOR Content Curator",
    }

    for attempt in range(1, total_attempts + 1):
        try:
            response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            body = response.json()
            content = (
                body.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            parsed = extract_json_from_text(content)
            if parsed is not None:
                return parsed
            safe_print(f"⚠️ Попытка {attempt}/{total_attempts}: невалидный JSON от OpenRouter")
        except requests.exceptions.RequestException as exc:
            safe_print(f"⚠️ Попытка {attempt}/{total_attempts}: ошибка запроса OpenRouter: {exc}")
        except ValueError as exc:
            safe_print(f"⚠️ Попытка {attempt}/{total_attempts}: ошибка декодирования JSON: {exc}")

    safe_print("❌ OpenRouter не вернул валидный JSON после всех попыток")
    return None


def curate_reviews(raw_reviews: List[Dict[str, Any]], count: int = 5) -> List[Dict[str, str]]:
    safe_print("🔍 Отбираем и редактируем отзывы...")
    if not raw_reviews:
        return []

    source = json.dumps(raw_reviews[:30], ensure_ascii=False, indent=2)
    system = "Ты редактор контента сайта салона красоты. Возвращай только валидный JSON."
    user = (
        "Из списка отзывов выбери максимум {count} лучших: конкретных, эмоциональных, читаемых. "
        "Исправь пунктуацию и орфографию, но не меняй смысл. "
        "Если имя странное/пустое, замени на нейтральное русское имя. "
        "Верни JSON формата {{\"reviews\":[{{\"author\":\"...\",\"text\":\"...\"}}]}}. "
        "Не добавляй ничего вне JSON. "
        "Отзывы:\n{source}"
    ).format(count=count, source=source)

    parsed = call_openrouter_json(system, user)
    if isinstance(parsed, dict) and isinstance(parsed.get("reviews"), list):
        cleaned: List[Dict[str, str]] = []
        for i, item in enumerate(parsed["reviews"]):
            if not isinstance(item, dict):
                continue
            text = compact_text(str(item.get("text") or ""))
            if len(text) < 8:
                continue
            author = compact_text(str(item.get("author") or "")) or fallback_author_name(None, i)
            cleaned.append({"author": author, "text": text})
        if cleaned:
            safe_print(f"✅ Отобрано отзывов: {len(cleaned[:count])}")
            return cleaned[:count]

    fallback: List[Dict[str, str]] = []
    for i, item in enumerate(raw_reviews[:count]):
        fallback.append(
            {
                "author": fallback_author_name(str(item.get("author") or ""), i),
                "text": compact_text(str(item.get("text") or "")),
            }
        )
    safe_print("⚠️ Использован fallback для отзывов")
    return fallback


def generate_tagline(business_name: str, city: str, source_tagline: str = "") -> str:
    safe_print("🔍 Готовим слоган...")
    system = "Ты бренд-копирайтер. Возвращай только JSON."
    if source_tagline:
        user = (
            "Отполируй слоган салона, сохрани смысл, сделай короче и чище. "
            "Верни JSON: {{\"tagline\":\"...\"}}. Слоган: {s}"
        ).format(s=source_tagline)
    else:
        user = (
            "Сделай один лаконичный слоган для салона красоты \"{name}\" в городе {city}. "
            "Без клише и воды. Верни JSON: {{\"tagline\":\"...\"}}."
        ).format(name=business_name, city=city)

    parsed = call_openrouter_json(system, user)
    if isinstance(parsed, dict):
        value = compact_text(str(parsed.get("tagline") or ""))
        if value:
            safe_print("✅ Слоган готов")
            return value

    safe_print("⚠️ Использован fallback для слогана")
    return "Эстетика, в которой важна каждая деталь."


def write_about_text(business_name: str, city: str, source_text: str = "") -> str:
    safe_print("🔍 Готовим блок 'О нас'...")
    system = "Ты редактор маркетинговых текстов для сайта. Возвращай только JSON."

    if source_text:
        user = (
            "Отредактируй текст раздела 'О нас': убери повторы и шероховатости, сохрани факты. "
            "Верни JSON: {{\"text\":\"...\"}}. Текст: {src}"
        ).format(src=source_text)
    else:
        user = (
            "Напиши 2-3 предложения для раздела 'О нас' салона \"{name}\" в городе {city}. "
            "Тон: премиально, но без пафоса. Верни JSON: {{\"text\":\"...\"}}."
        ).format(name=business_name, city=city)

    parsed = call_openrouter_json(system, user)
    if isinstance(parsed, dict):
        text = compact_text(str(parsed.get("text") or ""))
        if text:
            safe_print("✅ Блок 'О нас' готов")
            return text

    safe_print("⚠️ Использован fallback для блока 'О нас'")
    return (
        f"{business_name} в {city} — пространство эстетичного сервиса и аккуратной заботы о деталях. "
        "Мы собираем востребованные услуги в одном месте, чтобы визит был комфортным и предсказуемым по результату."
    )


def sanitize_service_title(title: str) -> str:
    value = compact_text(title)
    value = re.sub(r"(?i)варьируется[^.,;:]*", "", value)
    value = re.sub(r"\b\d{3,}\s*[р₽]?\b", "", value)
    value = compact_text(value)
    return value


def generate_services(business_name: str, city: str, category: str, count: int = 5) -> List[Dict[str, str]]:
    safe_print("🔍 Формируем услуги...")
    system = "Ты контент-менеджер сайта салона красоты. Возвращай только JSON."
    user = (
        "Собери {count} популярных услуг для салона \"{name}\" ({city}), категория: {category}. "
        "Формат JSON: {{\"services\":[{{\"title\":\"...\",\"short\":\"...\",\"description\":\"...\",\"priceFrom\":\"от 1500 ₽\"}}]}}. "
        "Без мусорных формулировок, без 'варьируется от ...', title только название услуги."
    ).format(count=count, name=business_name, city=city, category=category)

    parsed = call_openrouter_json(system, user)
    if isinstance(parsed, dict) and isinstance(parsed.get("services"), list):
        out: List[Dict[str, str]] = []
        for item in parsed["services"]:
            if not isinstance(item, dict):
                continue
            title = sanitize_service_title(str(item.get("title") or ""))
            short = compact_text(str(item.get("short") or ""))
            description = compact_text(str(item.get("description") or ""))
            price = compact_text(str(item.get("priceFrom") or ""))
            if not title:
                continue
            if not short:
                short = title
            if not description:
                description = short
            if not price:
                price = "по запросу"
            out.append({"title": title, "short": short, "description": description, "priceFrom": price})
        if out:
            safe_print(f"✅ Услуги готовы: {len(out[:count])}")
            return out[:count]

    fallback = [
        {"title": "Маникюр", "short": "Аккуратный маникюр", "description": "Форма, покрытие и чистый результат с учетом пожеланий.", "priceFrom": "от 1500 ₽"},
        {"title": "Окрашивание волос", "short": "Цвет и уход", "description": "Подбираем оттенок и технику под тип волос и желаемый образ.", "priceFrom": "от 3500 ₽"},
        {"title": "Оформление бровей", "short": "Выразительная форма", "description": "Коррекция и оттенок для естественного и гармоничного результата.", "priceFrom": "от 900 ₽"},
        {"title": "Макияж", "short": "Образ под событие", "description": "Дневной, вечерний или праздничный макияж под ваш формат мероприятия.", "priceFrom": "от 2000 ₽"},
        {"title": "Укладка", "short": "Финальный акцент", "description": "Легкая или объемная укладка с учетом длины и структуры волос.", "priceFrom": "от 1800 ₽"},
    ]
    safe_print("⚠️ Использован fallback для услуг")
    return fallback[:count]


def generate_faq(business_name: str, city: str, services: List[Dict[str, str]], count: int = 5) -> List[Dict[str, str]]:
    safe_print("🔍 Генерируем FAQ...")
    system = "Ты редактор FAQ для сайта салона. Возвращай только JSON."
    service_titles = [s.get("title", "") for s in services if isinstance(s, dict)]
    user = (
        "Сделай {count} FAQ-пар для салона \"{name}\" ({city}). "
        "Учитывай услуги: {services}. "
        "Короткие и полезные ответы. Формат JSON: {{\"faq\":[{{\"q\":\"...\",\"a\":\"...\"}}]}}."
    ).format(count=count, name=business_name, city=city, services=", ".join(service_titles[:8]) or "бьюти-услуги")

    parsed = call_openrouter_json(system, user)
    if isinstance(parsed, dict) and isinstance(parsed.get("faq"), list):
        out: List[Dict[str, str]] = []
        for item in parsed["faq"]:
            if not isinstance(item, dict):
                continue
            q = compact_text(str(item.get("q") or ""))
            a = compact_text(str(item.get("a") or ""))
            if q and a:
                out.append({"q": q, "a": a})
        if out:
            safe_print(f"✅ FAQ готов: {len(out[:count])}")
            return out[:count]

    fallback = [
        {"q": "Нужна ли предварительная запись?", "a": "Да, предварительная запись помогает выбрать удобное время и мастера без ожидания."},
        {"q": "Сколько длится процедура?", "a": "Длительность зависит от услуги, администратор подскажет точное время при записи."},
        {"q": "Можно ли выбрать мастера?", "a": "Да, при записи можно выбрать мастера по специализации и свободным слотам."},
        {"q": "Как узнать итоговую стоимость?", "a": "Мы заранее согласуем состав услуги и ориентировочную стоимость до начала работы."},
        {"q": "Что взять с собой на первый визит?", "a": "Достаточно описать ожидаемый результат, при желании можно показать фото-референсы."},
    ]
    safe_print("⚠️ Использован fallback для FAQ")
    return fallback[:count]


async def curate_lead(lead_id: int) -> Optional[Dict[str, Any]]:
    safe_print(f"🔍 Запуск Content Curator для lead_id={lead_id}")

    async with get_async_session() as session:
        result = await session.execute(select(Lead).where(Lead.id == lead_id))
        lead = result.scalar_one_or_none()

    if not lead:
        safe_print(f"❌ Лид с ID={lead_id} не найден")
        return None

    business_name = compact_text(lead.name or f"Lead {lead_id}")
    city = detect_city(lead.address)
    category = compact_text(lead.category or "other")
    raw_reviews = parse_raw_reviews(lead.raw_reviews)

    safe_print(f"🎯 Найден лид: {business_name} | category={category} | reviews={len(raw_reviews)}")

    about_source = extract_about_source(lead.audit_notes)
    tagline_source = extract_tagline_source(lead.audit_notes)

    tagline = generate_tagline(business_name, city, source_tagline=tagline_source)
    about_text = write_about_text(business_name, city, source_text=about_source)
    curated_reviews = curate_reviews(raw_reviews, count=5)
    service_cards = generate_services(business_name, city, category, count=5)
    faq_items = generate_faq(business_name, city, service_cards, count=5)

    slug = slugify_name(business_name, lead_id)
    output_payload: Dict[str, Any] = {
        "slug": slug,
        "meta": {
            "name": business_name,
            "tagline": tagline,
        },
        "about": about_text,
        "reviews": curated_reviews,
        "faq": faq_items,
        "services": service_cards,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{slug}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, ensure_ascii=False, indent=2)

    safe_print(f"✅ Curated JSON сохранён: {out_path}")
    return {"lead_id": lead_id, "slug": slug, "output_path": str(out_path), "data": output_payload}


def main() -> None:
    init_env()

    parser = argparse.ArgumentParser(description="Content Curator for Radar leads")
    parser.add_argument("lead_id", type=int, help="Lead ID from SQLite database")
    args = parser.parse_args()

    safe_print("=" * 60)
    safe_print("🚀 Content Curator")
    safe_print(f"🤖 Модель: {OPENROUTER_MODEL}")
    safe_print(f"🌐 OpenRouter: {OPENROUTER_URL}")
    safe_print(f"🔑 OPENROUTER_API_KEY: {'yes' if bool(os.getenv('OPENROUTER_API_KEY')) else 'no'}")
    safe_print(f"📁 Output: {OUTPUT_DIR}")
    safe_print("=" * 60)

    result = asyncio.run(curate_lead(args.lead_id))
    if not result:
        safe_print("❌ Курация не выполнена")
        return

    safe_print("\n🏁 Курация завершена успешно")
    safe_print(f"📄 Файл: {result['output_path']}")


if __name__ == "__main__":
    main()
