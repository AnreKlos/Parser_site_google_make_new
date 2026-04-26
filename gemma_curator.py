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
from sqlalchemy import select

from db.database import get_async_session
from db.models import Lead


# --- НАСТРОЙКИ CURATOR ---
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "gemma4:e4b"
REQUEST_TIMEOUT = 60
MAX_JSON_RETRIES = 2
OUTPUT_DIR = Path(__file__).parent / "data" / "curated"

FALLBACK_AUTHORS = ["Мария", "Анна", "Ольга", "Елена", "Наталья", "Татьяна"]


def safe_print(text: str) -> None:
    """Безопасный print с защитой от charmap ошибок на Windows."""
    try:
        print(text, flush=True)
    except (UnicodeEncodeError, UnicodeError):
        print(text.encode("cp1251", errors="replace").decode("cp1251"), flush=True)


def extract_json_from_text(text: str) -> Optional[Any]:
    """Пытается извлечь валидный JSON (объект/массив) из ответа модели."""
    if text is None:
        return None

    candidate = text.strip()
    if not candidate:
        return None

    # 1) Быстрый путь
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        pass

    # 2) Вырезаем markdown-блок, если есть
    md_match = re.search(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL | re.IGNORECASE)
    if md_match:
        block = md_match.group(1).strip()
        try:
            return json.loads(block)
        except (json.JSONDecodeError, TypeError):
            pass

    # 3) Ищем JSON объект
    obj_match = re.search(r"\{[\s\S]*\}", candidate)
    if obj_match:
        obj_text = obj_match.group(0)
        try:
            return json.loads(obj_text)
        except (json.JSONDecodeError, TypeError):
            pass

    # 4) Ищем JSON массив
    arr_match = re.search(r"\[[\s\S]*\]", candidate)
    if arr_match:
        arr_text = arr_match.group(0)
        try:
            return json.loads(arr_text)
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def call_ollama_json(prompt: str) -> Optional[Any]:
    """Вызывает Ollama и возвращает распарсенный JSON с ретраями."""
    total_attempts = MAX_JSON_RETRIES + 1
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }

    for attempt in range(1, total_attempts + 1):
        try:
            response = requests.post(OLLAMA_URL, json=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            raw_text = data.get("response", "")
            parsed = extract_json_from_text(raw_text)
            if parsed is not None:
                return parsed

            safe_print(f"⚠️ Попытка {attempt}/{total_attempts}: невалидный JSON от Gemma")
        except requests.exceptions.RequestException as exc:
            safe_print(f"⚠️ Попытка {attempt}/{total_attempts}: ошибка запроса к Ollama: {exc}")
        except ValueError as exc:
            safe_print(f"⚠️ Попытка {attempt}/{total_attempts}: ошибка декодирования ответа: {exc}")

    safe_print("❌ Gemma не вернула валидный JSON после всех попыток")
    return None


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

    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return "вашем городе"

    first = re.sub(r"^г\.?\s*", "", parts[0], flags=re.IGNORECASE).strip()
    if first and not re.match(r"^\d", first):
        return first
    return "вашем городе"


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def fallback_author_name(raw_author: Optional[str], index: int) -> str:
    author = compact_text(raw_author or "")
    if author and re.search(r"[А-Яа-я]", author):
        first_word = author.split()[0]
        first_word = re.sub(r"[^А-Яа-яA-Za-z]", "", first_word)
        if first_word:
            return first_word.capitalize()

    return FALLBACK_AUTHORS[index % len(FALLBACK_AUTHORS)]


def parse_raw_reviews(raw_reviews: Optional[str]) -> List[Dict[str, Any]]:
    if not raw_reviews:
        return []

    try:
        parsed = json.loads(raw_reviews)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(parsed, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for i, review in enumerate(parsed):
        if not isinstance(review, dict):
            continue

        text = compact_text(
            str(
                review.get("text")
                or review.get("review")
                or review.get("content")
                or ""
            )
        )
        if not text:
            continue

        author = (
            review.get("author")
            or review.get("author_name")
            or review.get("name")
            or review.get("user")
            or review.get("reviewer")
            or ""
        )

        normalized.append(
            {
                "author": compact_text(str(author)),
                "text": text,
                "rating": review.get("rating"),
                "index": i,
            }
        )

    return normalized


def _select_review_indices(reviews: List[Dict[str, Any]], top_k: int) -> List[int]:
    if not reviews:
        return []

    top_k = max(1, min(top_k, len(reviews)))

    reviews_json = json.dumps(
        [{"i": idx, "author": r.get("author", ""), "text": r.get("text", "")} for idx, r in enumerate(reviews)],
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""Ты строгий куратор отзывов премиум-салона красоты.
Выбери {top_k} самых эмоциональных и содержательных отзывов.
Игнорируй сухие фразы без деталей.
Верни СТРОГО JSON: {{"indices": [..]}}.
Только индексы из списка ниже, без повторов.
Отзывы:
{reviews_json}"""

    parsed = call_ollama_json(prompt)
    if not isinstance(parsed, dict):
        return list(range(top_k))

    indices = parsed.get("indices")
    if not isinstance(indices, list):
        return list(range(top_k))

    result: List[int] = []
    for i in indices:
        if isinstance(i, int) and 0 <= i < len(reviews) and i not in result:
            result.append(i)

    if not result:
        return list(range(top_k))

    return result[:top_k]


def _polish_reviews(reviews: List[Dict[str, Any]], count: int) -> List[Dict[str, str]]:
    if not reviews:
        return []

    chunk_json = json.dumps(
        [{"author": r.get("author", ""), "text": r.get("text", "")} for r in reviews],
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""Ты редактор отзывов для сайта премиум-салона красоты.
Мягко почисти орфографию и пунктуацию, не меняя смысл.
Имена авторов обобщи в нейтральные русские.
Верни СТРОГО JSON: {{"reviews": [{{"author":"...","text":"..."}}]}}.
Нужно максимум {count} отзывов.
Данные:
{chunk_json}"""

    parsed = call_ollama_json(prompt)
    if isinstance(parsed, dict) and isinstance(parsed.get("reviews"), list):
        polished: List[Dict[str, str]] = []
        for i, item in enumerate(parsed["reviews"]):
            if not isinstance(item, dict):
                continue
            text = compact_text(str(item.get("text", "")))
            if not text:
                continue
            author = compact_text(str(item.get("author", ""))) or fallback_author_name(None, i)
            polished.append({"author": author, "text": text})
        if polished:
            return polished[:count]

    # Fallback без Gemma
    fallback: List[Dict[str, str]] = []
    for i, r in enumerate(reviews[:count]):
        fallback.append(
            {
                "author": fallback_author_name(str(r.get("author", "")), i),
                "text": compact_text(str(r.get("text", ""))),
            }
        )
    return fallback


def select_best_reviews(reviews_list: List[Dict[str, Any]], count: int = 5) -> List[Dict[str, str]]:
    """
    Выбирает лучшие отзывы через Gemma.
    Если отзывов >15: батчи по 10 -> топ-3 из каждого -> финальный топ.
    """
    safe_print("🔍 Отбираем лучшие отзывы...")

    if not reviews_list:
        safe_print("⚠️ Отзывы не найдены, блок reviews будет пустым")
        return []

    count = max(1, count)

    if len(reviews_list) <= 15:
        best_idx = _select_review_indices(reviews_list, min(count, len(reviews_list)))
        selected = [reviews_list[i] for i in best_idx]
        curated = _polish_reviews(selected, min(count, len(selected)))
        safe_print(f"✅ Отобрано отзывов: {len(curated)}")
        return curated

    finalists: List[Dict[str, Any]] = []
    for batch_start in range(0, len(reviews_list), 10):
        batch = reviews_list[batch_start : batch_start + 10]
        top_idx = _select_review_indices(batch, min(3, len(batch)))
        finalists.extend(batch[i] for i in top_idx)

    if len(finalists) > count:
        final_idx = _select_review_indices(finalists, min(count, len(finalists)))
        selected_final = [finalists[i] for i in final_idx]
    else:
        selected_final = finalists[:count]

    curated = _polish_reviews(selected_final, min(count, len(selected_final)))
    safe_print(f"✅ Отобрано отзывов: {len(curated)}")
    return curated


def extract_about_source(audit_notes: Optional[str]) -> str:
    notes = audit_notes or ""
    patterns = [
        r"(?:о\s*нас|about)\s*[:\-]\s*(.{40,600})",
        r"(?:описание|description)\s*[:\-]\s*(.{40,600})",
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


def write_about_text(
    business_name: str,
    services: List[str],
    city: str,
    tone: str = "premium",
    source_text: str = "",
) -> str:
    safe_print("🔍 Готовим блок 'О нас'...")
    services_text = ", ".join(services[:6]) if services else "beauty-услуги"

    if source_text:
        prompt = f"""Ты копирайтер премиум-салонов красоты.
Отполируй текст 'О нас' без изменения смысла.
Убери только явные ошибки и шероховатости.
Верни СТРОГО JSON: {{"text": "..."}}.
Текст:
{source_text}"""
    else:
        prompt = f"""Ты копирайтер премиум-салонов красоты.
Напиши 2-3 предложения для секции 'О нас'.
Салон: {business_name}, город: {city}, услуги: {services_text}.
Стиль: {tone}, элегантно, без штампов и эмодзи.
Верни СТРОГО JSON: {{"text": "..."}}."""

    parsed = call_ollama_json(prompt)
    if isinstance(parsed, dict):
        text = compact_text(str(parsed.get("text", "")))
        if text:
            safe_print("✅ Блок 'О нас' готов")
            return text

    fallback = f"{business_name} в {city} — пространство эстетичного сервиса и аккуратной заботы о деталях. Мы собрали востребованные beauty-услуги в одном месте, чтобы визит был комфортным и предсказуемым по результату."
    safe_print("⚠️ Использован fallback для блока 'О нас'")
    return fallback


def generate_faq(business_name: str, services: List[str], city: str, count: int = 5) -> List[Dict[str, str]]:
    safe_print("🔍 Генерируем FAQ...")
    services_text = ", ".join(services[:8]) if services else "beauty-услуги"

    prompt = f"""Ты копирайтер премиум-салонов красоты.
Сгенерируй {count} пар FAQ для сайта салона {business_name} ({city}).
Услуги: {services_text}.
Коротко, конкретно, без воды и сленга.
Верни СТРОГО JSON: {{"faq": [{{"q":"...","a":"..."}}]}}."""

    parsed = call_ollama_json(prompt)
    if isinstance(parsed, dict) and isinstance(parsed.get("faq"), list):
        items: List[Dict[str, str]] = []
        for item in parsed["faq"]:
            if not isinstance(item, dict):
                continue
            q = compact_text(str(item.get("q", "")))
            a = compact_text(str(item.get("a", "")))
            if q and a:
                items.append({"q": q, "a": a})
        if items:
            safe_print(f"✅ FAQ готов: {len(items[:count])} шт.")
            return items[:count]

    fallback = [
        {
            "q": "Нужно ли записываться заранее?",
            "a": "Да, предварительная запись помогает выбрать удобное время и мастера без ожидания.",
        },
        {
            "q": "Сколько длится процедура?",
            "a": "Время зависит от выбранной услуги, администратор уточнит длительность при записи.",
        },
        {
            "q": "Как подготовиться к визиту?",
            "a": "Достаточно прийти за 5-10 минут до начала и сообщить мастеру ваши пожелания.",
        },
        {
            "q": "Можно ли выбрать мастера?",
            "a": "Да, при записи можно выбрать конкретного мастера по его специализации.",
        },
        {
            "q": "Как узнать итоговую стоимость?",
            "a": "Мы заранее согласовываем объём работ и ориентировочную стоимость до начала процедуры.",
        },
    ]
    safe_print("⚠️ Использован fallback для FAQ")
    return fallback[:count]


def generate_services_seed(category: str, business_name: str, city: str, count: int = 5) -> List[Dict[str, str]]:
    safe_print("🔍 Формируем список услуг по категории...")

    prompt = f"""Ты контент-менеджер премиум-салона.
Собери {count} типовых услуг для категории '{category}'.
Салон: {business_name}, город: {city}.
Верни СТРОГО JSON: {{"services": [{{"title":"...","priceFrom":"от 0000 ₽"}}]}}.
Названия услуг — на русском."""

    parsed = call_ollama_json(prompt)
    if isinstance(parsed, dict) and isinstance(parsed.get("services"), list):
        result: List[Dict[str, str]] = []
        for item in parsed["services"]:
            if not isinstance(item, dict):
                continue
            title = compact_text(str(item.get("title", "")))
            if not title:
                continue
            price_from = compact_text(str(item.get("priceFrom", "от 1500 ₽")))
            result.append({"title": title, "priceFrom": price_from or "от 1500 ₽"})
        if result:
            safe_print(f"✅ Базовые услуги готовы: {len(result[:count])} шт.")
            return result[:count]

    # Fallback для тестового сценария по категории
    category_low = (category or "").lower()
    if "salon" in category_low or "крас" in category_low or "beauty" in category_low:
        fallback_titles = ["Маникюр", "Парикмахерские услуги", "Оформление бровей", "Наращивание ресниц", "Макияж"]
    else:
        fallback_titles = ["Базовая услуга", "Премиум-уход", "Комплексная процедура", "Экспресс-формат", "Индивидуальная консультация"]

    fallback = [{"title": t, "priceFrom": "от 1500 ₽"} for t in fallback_titles[:count]]
    safe_print("⚠️ Использован fallback для списка услуг")
    return fallback


def write_service_descriptions(services_list: List[Dict[str, str]]) -> List[Dict[str, str]]:
    safe_print("🔍 Пишем описания услуг...")
    if not services_list:
        return []

    services_json = json.dumps(services_list, ensure_ascii=False, indent=2)
    prompt = f"""Ты копирайтер премиум-салонов красоты.
Для каждой услуги напиши:
- short: 1 короткая фраза
- description: 2-3 предложения
Стиль элегантный, без клише и сленга.
Верни СТРОГО JSON: {{"services": [{{"title":"...","short":"...","description":"...","priceFrom":"..."}}]}}.
Услуги:
{services_json}"""

    parsed = call_ollama_json(prompt)
    if isinstance(parsed, dict) and isinstance(parsed.get("services"), list):
        result: List[Dict[str, str]] = []
        for item in parsed["services"]:
            if not isinstance(item, dict):
                continue
            title = compact_text(str(item.get("title", "")))
            short = compact_text(str(item.get("short", "")))
            description = compact_text(str(item.get("description", "")))
            price_from = compact_text(str(item.get("priceFrom", ""))) or "от 1500 ₽"
            if title and short and description:
                result.append(
                    {
                        "title": title,
                        "short": short,
                        "description": description,
                        "priceFrom": price_from,
                    }
                )
        if result:
            safe_print(f"✅ Описания услуг готовы: {len(result)} шт.")
            return result

    fallback: List[Dict[str, str]] = []
    for item in services_list:
        title = compact_text(str(item.get("title", "Услуга")))
        price_from = compact_text(str(item.get("priceFrom", "от 1500 ₽"))) or "от 1500 ₽"
        fallback.append(
            {
                "title": title,
                "short": "Точный акцент на результате и комфорте.",
                "description": "Процедура выполняется с вниманием к деталям и индивидуальным пожеланиям. Формат подходит для тех, кто ценит аккуратный результат и спокойный сервис.",
                "priceFrom": price_from,
            }
        )

    safe_print("⚠️ Использован fallback для описаний услуг")
    return fallback


def generate_tagline(
    business_name: str,
    services: List[str],
    city: str,
    source_tagline: str = "",
) -> str:
    safe_print("🔍 Готовим слоган...")
    services_text = ", ".join(services[:5]) if services else "beauty-услуги"

    if source_tagline:
        prompt = f"""Ты редактор премиум-бренда.
Отполируй слоган без потери смысла.
Сделай фразу лаконичной и элегантной.
Верни СТРОГО JSON: {{"tagline":"..."}}.
Слоган:
{source_tagline}"""
    else:
        prompt = f"""Ты копирайтер премиум-салонов красоты.
Создай 1 короткий слоган для {business_name} ({city}).
Услуги: {services_text}.
Без штампов, без эмодзи.
Верни СТРОГО JSON: {{"tagline":"..."}}."""

    parsed = call_ollama_json(prompt)
    if isinstance(parsed, dict):
        tagline = compact_text(str(parsed.get("tagline", "")))
        if tagline:
            safe_print("✅ Слоган готов")
            return tagline

    fallback = "Эстетика, в которой важна каждая деталь."
    safe_print("⚠️ Использован fallback для слогана")
    return fallback


async def curate_lead(lead_id: int) -> Optional[Dict[str, Any]]:
    """Главная функция курации контента по lead_id."""
    safe_print(f"🔍 Запуск Gemma Curator для lead_id={lead_id}")

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

    # audit_notes используется как внутренний контекст для Gemma
    about_source = extract_about_source(lead.audit_notes)
    tagline_source = extract_tagline_source(lead.audit_notes)

    services_seed = generate_services_seed(category, business_name, city, count=5)
    service_titles = [s["title"] for s in services_seed]

    tagline = generate_tagline(business_name, service_titles, city, source_tagline=tagline_source)
    about_text = write_about_text(
        business_name=business_name,
        services=service_titles,
        city=city,
        tone="premium",
        source_text=about_source,
    )
    curated_reviews = select_best_reviews(raw_reviews, count=5)
    faq_items = generate_faq(business_name, service_titles, city, count=5)
    service_cards = write_service_descriptions(services_seed)

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
    parser = argparse.ArgumentParser(description="Gemma Content Curator for Radar leads")
    parser.add_argument("lead_id", type=int, help="Lead ID from SQLite database")
    args = parser.parse_args()

    safe_print("=" * 60)
    safe_print("🚀 Gemma Content Curator")
    safe_print(f"🤖 Модель: {MODEL_NAME}")
    safe_print(f"🌐 Ollama: {OLLAMA_URL}")
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
