#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from pathlib import Path

# Добавляем корень проекта в путь для импортов (ДО остальных импортов!)
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from sqlalchemy import select

from config import settings
from db.database import get_async_session
from db.models import Lead
from utils import slugify_name, compact_text, detect_city, fallback_author_name, is_junk_service_title, extract_json_from_text, FALLBACK_AUTHORS

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data" / "curated"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DATA_BLOCK_TEMPLATE = """## ДАННЫЕ О САЛОНЕ (используй ТОЛЬКО их, не выдумывай):
Название: {name}
Город: {city} | Адрес: {address}
Тип бизнеса: {business_type}
Часы работы: {hours}
Категория: {category}
Реальные услуги этого салона (используй только их, не придумывай новых): {real_services}
Реальные цены с Яндекс.Карт: {real_prices}
Рейтинг: {rating} из 5 ({reviews_count} отзывов на Яндекс.Картах)
Позиционирование с сайта: {tagline}
---
ПРАВИЛА (нарушение = провал задачи):
- Запрещённые слова: оазис, элегантность, безупречный, уникальный, премиум, доверьтесь, индивидуальный подход, команда профессионалов, уютная атмосфера, высокое качество, профессиональный, мировые стандарты
- Если Тип бизнеса = "сеть салонов красоты": ЗАПРЕЩЁНО использовать слова "моностудия", "одна студия", "одиночная студия". Используй формулировки "сеть салонов красоты", "сеть студий красоты", "салон сети «{name}» в городе {city}".
- Если Тип бизнеса = "студия красоты": можно использовать "студия красоты", "студия по созданию образа". Слово "моностудия" не должно быть жёстко зашито как шаблон.
- Запрещённые темы: штрафы, предоплата, условия отмены, внутренние правила салона
- Не придумывай адреса, услуги, мастеров которых нет в данных выше
- Говори языком подруги, не менеджера и не юриста
- Запрещено начинать description с «Вы будете», «Ваши», «Вы получите», «Вы станете»
- Запрещены глаголы неуверенности: постараемся, попробуем, возможно, надеемся, попытаемся
- Каждый элемент списка должен начинаться с уникальной синтаксической конструкции — не повторять паттерн соседних пунктов
- Запрещено формулировать страх клиентки даже с отрицанием.
  Нельзя: «не переживай что...», «не бойся что...», «без риска что...»
  Нужно: просто назови факт который снимает страх
  Плохо:  «Не переживай что макияж не продержится»
  Хорошо: «Стойкие формулы — макияж держится 12–16 часов»
- Запрещено упоминать требования к клиентке перед процедурой:
  «чистые волосы», «без лака», «после душа», «заранее снять» — это внутренние правила салона, не УТП, клиентке читать это некомфортно
- Запрещено слово «идеально» и производные «идеальный», «идеальные» —
  заменяй конкретным результатом: не «идеальные брови» а «форма держится 3 недели»
- Запрещено заканчивать предложение словами снимающими страх через отрицание:
  «так что можешь не переживать», «не беспокойся о», «не думай о» —
  заканчивай на факте: «Макияж держится 12–16 часов.» — точка.
- title услуги: убирай цифры и спецсимволы в конце названия
  («Оформление бровей1» → «Оформление бровей», «Маникюр2» → «Маникюр»)
- Запрещено: «подчеркнёт твою индивидуальность», «раскроет твой потенциал»,
  «подчеркнёт твою красоту» — заменяй конкретным результатом
- Генерируй только те услуги которые есть в поле real_services.
  Если real_services не пустой — не добавляй услуги которых там нет."""


def init_env() -> None:
    load_dotenv()


def is_chain(lead: Any, yandex_data: Dict[str, Any]) -> bool:
    """Определяет, является ли бизнес сетью салонов.
    
    Returns True если:
    - Уникальных адресов >= 2 ИЛИ
    - Уникальных телефонов >= 3
    
    Args:
        lead: Lead object from DB
        yandex_data: Yandex enrichment data
    """
    addresses = set()
    phones = set()
    
    # Собираем адреса и телефоны из БД
    if lead.address:
        addresses.add(compact_text(lead.address))
    if lead.phone:
        # Разбиваем телефоны по разделителям
        phone_str = str(lead.phone)
        for p in re.split(r'[,\s;]+', phone_str):
            p_clean = re.sub(r'[^\d]', '', p)
            if len(p_clean) >= 10:
                phones.add(p_clean)
    
    # Собираем адреса и телефоны из yandex (может быть несколько карточек)
    yandex_info = yandex_data.get("yandex", {})
    
    # Основной адрес из yandex
    if yandex_info.get("address"):
        addresses.add(compact_text(yandex_info["address"]))
    
    # Телефоны из yandex (могут быть списком)
    yandex_phones = yandex_info.get("phones", [])
    if isinstance(yandex_phones, list):
        for phone in yandex_phones:
            p_clean = re.sub(r'[^\d]', '', str(phone))
            if len(p_clean) >= 10:
                phones.add(p_clean)
    elif isinstance(yandex_phones, str):
        for p in re.split(r'[,\s;]+', yandex_phones):
            p_clean = re.sub(r'[^\d]', '', p)
            if len(p_clean) >= 10:
                phones.add(p_clean)
    
    # Дополнительные адреса из yandex
    additional_addresses = yandex_info.get("additional_addresses", [])
    if isinstance(additional_addresses, list):
        for addr in additional_addresses:
            if addr:
                addresses.add(compact_text(addr))
    
    # Дополнительные филиалы из yandex (если есть)
    branches = yandex_data.get("yandex", {}).get("branches", [])
    if isinstance(branches, list):
        for branch in branches:
            if isinstance(branch, dict):
                if branch.get("address"):
                    addresses.add(compact_text(branch["address"]))
                if branch.get("phone"):
                    for p in re.split(r'[,\s;]+', str(branch["phone"])):
                        p_clean = re.sub(r'[^\d]', '', p)
                        if len(p_clean) >= 10:
                            phones.add(p_clean)
    
    # Определяем сеть
    unique_addresses = len([a for a in addresses if a and len(a) > 5])
    unique_phones = len(phones)
    
    is_chain_result = unique_addresses >= 2 or unique_phones >= 3
    
    print(f"🔍 is_chain анализ: адресов={unique_addresses}, телефонов={unique_phones} -> {is_chain_result}")
    if is_chain_result:
        print(f"   Адреса: {list(addresses)}")
        print(f"   Телефоны: {list(phones)}")
    
    return is_chain_result


def is_junk_service_title_enhanced(title: str) -> tuple[bool, str]:
    """Расширенная проверка на мусорное название услуги.
    
    Returns (is_junk, reason)
    """
    if not title or len(title.strip()) < 2:
        return True, "пустое название"
    
    title = title.strip()
    
    # Правило 1: Длиннее 100 символов и содержит 2+ признаков прайса
    price_indicators = 0
    if len(title) > 100:
        if '₽' in title or 'р.' in title or 'руб' in title:
            price_indicators += 1
        if '%' in title:
            price_indicators += 1
        if '–' in title or '-' in title:
            price_indicators += 1
        if re.search(r'\d+\s*[-–]\s*\d+', title):  # диапазон цен
            price_indicators += 1
        
        if price_indicators >= 2:
            return True, f"длинное название с {price_indicators} признаками прайса"
    
    # Правило 2: Начинается с фрагментов прайса
    price_start_patterns = [
        r'^\s*(женской|мужской)\s+стрижки\s*:',
        r'^\s*стрижка\s*:\s*от',
        r'^\s*стрижка\s*:\s*\d+',
        r'^\s*женской\s+стрижки:\d+',
        r'^\s*мужской\s+стрижки:\d+',
        r'^\s*окрашивание:\d+',
        r'^\s*маникюр:\d+',
        r'^\s*педикюр:\d+',
    ]
    
    for pattern in price_start_patterns:
        if re.match(pattern, title, re.IGNORECASE):
            return True, f"начинается с фрагмента прайса: {pattern}"
    
    # Правило 3: Содержит склейку бренда/рейтинга/прайса
    # Пример: "Империя красоты4,4Стрижка: от"
    brand_rating_price_patterns = [
        r'[А-Яа-яёЁ\s]+[\d,]+\.\d+[А-Яа-яёЁ]',  # "Империя красоты4,4Стрижка"
        r'[А-Яа-яёЁ\s]+\d+,\d+\s*:',  # "Империя красоты4,4:"
        r'[А-Яа-яёЁ\s]+:\d+[-–]',  # "Стрижка:300-"
        r'\d+\.\d+\s*[А-Яа-яёЁ]',  # "4.4Стрижка"
    ]
    
    for pattern in brand_rating_price_patterns:
        if re.search(pattern, title):
            return True, f"склейка бренда/рейтинга/прайса: {pattern}"
    
    # Правило 4: Содержит только цифры и символы без букв
    if re.match(r'^[\d\s\-–₽%,.]+$', title):
        return True, "только цифры и символы"
    
    # Правило 5: Содержит мусорные фразы
    junk_phrases = [
        'женской стрижки:',
        'мужской стрижки:',
        'стрижки: от',
        'варьируется',
        'ВЫПОЛНЯЕТСЯ НА',
    ]
    
    for phrase in junk_phrases:
        if phrase.lower() in title.lower():
            return True, f"содержит мусорную фразу: {phrase}"
    
    return False, ""


def filter_junk_services(services: List[Dict[str, str]], max_count: int = 7) -> List[Dict[str, str]]:
    """Фильтрует мусорные услуги и ограничивает количество.
    
    Args:
        services: список услуг из LLM
        max_count: максимальное количество услуг в выводе
    
    Returns:
        Отфильтрованный список услуг
    """
    filtered = []
    junk_count = 0
    
    for service in services:
        if not isinstance(service, dict):
            continue
        
        title = service.get("title", "")
        is_junk, reason = is_junk_service_title_enhanced(title)
        
        if is_junk:
            print(f"⚠️ Пропущена мусорная услуга: '{title[:50]}...' ({reason})")
            junk_count += 1
            continue
        
        # Очищаем priceFrom от мусора
        price_from = service.get("priceFrom", "")
        if price_from:
            # Если priceFrom содержит мусор прайса - заменяем на уточнить при записи
            price_junk_patterns = [
                r'женской\s+стрижки:\d+[-–]',
                r'мужской\s+стрижки:\d+[-–]',
                r'стрижки:\d+[-–]\s*мужской',
                r'\d+[-–]\s*₽.*\d+[-–]',  # диапазон цен с другим диапазоном
            ]
            
            for pattern in price_junk_patterns:
                if re.search(pattern, price_from, re.IGNORECASE):
                    print(f"⚠️ Заменен мусорный priceFrom: '{price_from}' -> 'уточнить при записи'")
                    service["priceFrom"] = "уточнить при записи"
                    break
        
        filtered.append(service)
    
    print(f"🔍 Фильтр услуг: удалено {junk_count} мусорных, осталось {len(filtered)}")
    
    # Ограничиваем количество
    if len(filtered) > max_count:
        print(f"🔍 Ограничение услуг: {len(filtered)} -> {max_count}")
        filtered = filtered[:max_count]
    
    return filtered






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


def call_openrouter_json(system_prompt: str, user_prompt: str) -> Optional[Any]:
    api_key = settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY не найден в окружении (.env)")

    total_attempts = settings.max_json_retries + 1
    payload = {
        "model": settings.openrouter_model,
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
            response = requests.post(settings.openrouter_url, headers=headers, json=payload, timeout=settings.request_timeout)
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
            print(f"⚠️ Попытка {attempt}/{total_attempts}: невалидный JSON от OpenRouter")
        except requests.exceptions.RequestException as exc:
            print(f"⚠️ Попытка {attempt}/{total_attempts}: ошибка запроса OpenRouter: {exc}")
        except ValueError as exc:
            print(f"⚠️ Попытка {attempt}/{total_attempts}: ошибка декодирования JSON: {exc}")

    print("❌ OpenRouter не вернул валидный JSON после всех попыток")
    return None


def curate_reviews(raw_reviews: List[Dict[str, Any]], count: int = 5) -> List[Dict[str, str]]:
    print("🔍 Отбираем и редактируем отзывы...")
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
            print(f"✅ Отобрано отзывов: {len(cleaned[:count])}")
            return cleaned[:count]

    fallback: List[Dict[str, str]] = []
    for i, item in enumerate(raw_reviews[:count]):
        fallback.append(
            {
                "author": fallback_author_name(str(item.get("author") or ""), i),
                "text": compact_text(str(item.get("text") or "")),
            }
        )
    print("⚠️ Использован fallback для отзывов")
    return fallback


def generate_tagline(context: Dict[str, Any], source_tagline: str = "") -> str:
    print("🔍 Готовим слоган...")
    system = "Ты топ-копирайтер beauty-индустрии России. Специализация — Hero-заголовки. Возвращай только JSON."
    
    data_block = DATA_BLOCK_TEMPLATE.format(**context)
    
    if context.get("tagline"):
        user = data_block + """

## ЗАДАЧА: напиши tagline для H1 первого экрана.

ФОРМУЛА (выбери одну):
1. [Конкретная услуга] + [конкретный результат] + [город/без очередей]
2. [Главный страх] → [как мы его снимаем]
3. [Для кого] + [что делаем] + [ключное отличие]

ЭТАЛОННЫЕ ПРИМЕРЫ:
- "Маникюр с гарантией 7 дней — переделаем бесплатно. Брянск."
- "Свадебный образ за одно посещение. Результат обсуждаем до, не после."
- "Стойкий макияж для невесты — держится 16 часов или возвращаем деньги."

ДЛИНА: 8–12 слов. Без восклицательных знаков.
Верни JSON: {{\"tagline\":\"...\"}}"""
    else:
        user = data_block + """

## ЗАДАЧА: напиши tagline для H1 первого экрана.

ФОРМУЛА (выбери одну):
1. [Конкретная услуга] + [конкретный результат] + [город/без очередей]
2. [Главный страх] → [как мы его снимаем]
3. [Для кого] + [что делаем] + [ключное отличие]

ЭТАЛОННЫЕ ПРИМЕРЫ:
- "Маникюр с гарантией 7 дней — переделаем бесплатно. Брянск."
- "Свадебный образ за одно посещение. Результат обсуждаем до, не после."
- "Стойкий макияж для невесты — держится 16 часов или возвращаем деньги."

ДЛИНА: 8–12 слов. Без восклицательных знаков.
Верни JSON: {{\"tagline\":\"...\"}}"""

    parsed = call_openrouter_json(system, user)
    if isinstance(parsed, dict):
        value = compact_text(str(parsed.get("tagline") or ""))
        if value:
            print("✅ Слоган готов")
            return value

    print("⚠️ Использован fallback для слогана")
    return "Эстетика, в которой важна каждая деталь."


def write_about_text(context: Dict[str, Any], source_text: str = "") -> str:
    print("🔍 Готовим блок 'О нас'...")
    system = "Ты копирайтер beauty-сайтов. Знаешь реальные отзывы с 2GIS и iRecommend. Возвращай только JSON."
    
    data_block = DATA_BLOCK_TEMPLATE.format(**context)
    
    user = data_block + """

## ЗАДАЧА: напиши блок «О нас» — 2–3 предложения.

АРХИТЕКТУРА:
Предложение 1: Что конкретно делаем + для кого.
Предложение 2: Закрываем страх «сделают не то что хочу» — конкретным действием.
Предложение 3: Социальное доказательство из рейтинга если есть.

РЕАЛЬНЫЕ СТРАХИ КЛИЕНТОК (из 2GIS, iRecommend):
«Боюсь что не услышат. Сделают как им удобно, а не как я хочу.»
«Боюсь заражения. Хочу видеть что инструменты чистые.»
«Хочу знать цену заранее, а не узнавать в конце.»

ЭТАЛОН (структура обязательна, отступать нельзя):
Предложение 1: конкретные услуги из DATA_BLOCK — не «всё для красоты», не «широкий спектр»
Предложение 2: одно конкретное действие снимающее страх «не услышат» — без обещаний, только факт
Предложение 3: рейтинг + число отзывов как сухой факт, без восклицаний и интерпретаций

ПЛОХО → ХОРОШО:
«всё чтобы ты выглядела прекрасно» → «Маникюр, свадебный макияж и брови — в одном визите»
«обсудим пожелания чтобы избежать недопонимания» → «Перед процедурой уточняем форму, цвет и стиль — не после»
«это говорит о том что клиенты довольны!» → «5.0 из 5 — 128 отзывов на Яндекс.Картах»
«постараемся исправить» → «исправим бесплатно в течение 7 дней»

Верни JSON: {{\"text\":\"...\"}}"""

    parsed = call_openrouter_json(system, user)
    if isinstance(parsed, dict):
        text = compact_text(str(parsed.get("text") or ""))
        if text:
            print("✅ Блок 'О нас' готов")
            return text

    print("⚠️ Использован fallback для блока 'О нас'")
    return (
        f"{business_name} в {city} — пространство эстетичного сервиса и аккуратной заботы о деталях. "
        "Мы собираем востребованные услуги в одном месте, чтобы визит был комфортным и предсказуемым по результату."
    )


def normalize_service_title(title: str) -> str:
    """Нормализует title услуги, убирая мусор."""
    value = compact_text(title)
    # Убираем хвосты с ценами и пояснениями
    value = re.sub(r"(?i)варьируется[^.,;:]*", "", value)
    value = re.sub(r"\bот\s*\d+[\sр₽]*до\s*\d+[\sр₽]*", "", value)
    value = re.sub(r"\b\d{3,}\s*[р₽]?\b", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = compact_text(value)
    return value


def sanitize_service_title(title: str) -> str:
    value = compact_text(title)
    value = re.sub(r"(?i)варьируется[^.,;:]*", "", value)
    value = re.sub(r"\b\d{3,}\s*[р₽]?\b", "", value)
    value = compact_text(value)
    return value


def generate_services(context: Dict[str, Any], count: int = 5) -> List[Dict[str, str]]:
    print("🔍 Формируем услуги...")
    system = "Ты контент-редактор beauty-сайтов. Описания услуг закрывают страхи, не продают воздух. Возвращай только JSON."
    
    # Парсим real_prices из JSON
    real_prices_dict = {}
    try:
        real_prices_dict = json.loads(context.get("real_prices", "{}"))
    except (json.JSONDecodeError, TypeError):
        real_prices_dict = {}
    
    data_block = DATA_BLOCK_TEMPLATE.format(**context)
    
    user = data_block + f"""

## ЗАДАЧА: опиши {count} услуг из списка выше.

СТРУКТУРА каждой услуги:
- title: только название, без прилагательных-клише
- short: одна строка — что получит клиентка (не что мы делаем)
- description: 2 предложения: (1) конкретный результат (2) снятие одного страха
- priceFrom: «от Х ₽» если есть, иначе «уточнить при записи"
- priceFrom: если значение «1 ₽», «0 ₽», «1», пустая строка, null или любое число меньше 100 — заменить на «уточнить при записи»
- Если в списке есть две услуги одной категории (например свадебный и вечерний макияж) — description должны отличаться по структуре и акценту:
  Свадебный макияж → акцент: пробный визит + стойкость весь день
  Вечерний макияж  → акцент: скорость сборки + яркость образа
  Не копировать структуру соседней услуги даже частично
- Используй только name и price из real_services. description бери только если он описывает саму услугу, а не условия записи, требования к клиенту или контактные данные. Если description содержит телефон, адрес, инструкцию или предупреждение — оставь поле пустым.

Реальные цены с Яндекс.Карт: {context.get("yandex_prices", "")}

СТАРТ description — только этими конструкциями (каждая услуга своя, не повторять):
Маникюр:      «Покрытие держится...»
Окрашивание:  «Берёмся только за...»
Макияж:       «Пробный визит до...»
Брови:        «Форма подбирается...»
Причёска:     «Обсуждаем стойкость...»
Педикюр:      «Кожа стоп после...»
Ресницы:      «Изгиб и длина...»
Прочее:       начни с факта о результате, не с «Вы»

Маникюр: «Инструменты стерилизуются в автоклаве, одноразовые вскрываем при вас»
Окрашивание: «Берёмся только за работы в результате которых уверены — скажем честно если случай сложный»
Свадебный макияж: «Пробный визит до свадьбы — смотришь как держится, корректируем»
Брови: «Форма под твоё лицо, не по трафарету. Учитываем асимметрию»
Причёска: «Обсуждаем стойкость до начала — если нужно на 8 часов, скажем честно что получится»

Верни JSON: {{\"services\":[{{\"title\":\"...\",\"short\":\"...\",\"description\":\"...\",\"priceFrom\":\"...\"}}]}}"""

    parsed = call_openrouter_json(system, user)
    if isinstance(parsed, dict) and isinstance(parsed.get("services"), list):
        out: List[Dict[str, str]] = []
        for item in parsed["services"]:
            if not isinstance(item, dict):
                continue
            title = sanitize_service_title(str(item.get("title") or ""))
            title = normalize_service_title(title)
            
            # Фильтруем мусорные названия (базовая проверка)
            if is_junk_service_title(title):
                print(f"⚠️ Пропущена мусорная услуга (базовая): {title}")
                continue
            
            # Фильтруем мусорные названия (расширенная проверка)
            is_junk_enhanced, junk_reason = is_junk_service_title_enhanced(title)
            if is_junk_enhanced:
                print(f"⚠️ Пропущена мусорная услуга (расширенная): {title[:50]}... ({junk_reason})")
                continue
            
            short = compact_text(str(item.get("short") or ""))
            description = compact_text(str(item.get("description") or ""))
            
            # Ищем цену в real_services по совпадению названия
            price = None
            title_lower = title.lower()
            for real_name, real_price in real_prices_dict.items():
                if title_lower in real_name.lower() or real_name.lower() in title_lower:
                    price = compact_text(str(real_price))
                    break
            
            # Если цены нет в real_services — берем из LLM
            if not price:
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
        
        # Убираем дубли по title
        seen_titles = set()
        unique_out = []
        for item in out:
            title_lower = item["title"].lower()
            if title_lower not in seen_titles:
                seen_titles.add(title_lower)
                unique_out.append(item)
        
        # Применяем расширенный фильтр мусорных услуг
        filtered_services = filter_junk_services(unique_out, max_count=count)
        
        if filtered_services:
            print(f"✅ Услуги готовы: {len(filtered_services)}")
            return filtered_services

    fallback = [
        {"title": "Маникюр", "short": "Аккуратный маникюр", "description": "Форма, покрытие и чистый результат с учетом пожеланий.", "priceFrom": "от 1500 ₽"},
        {"title": "Окрашивание волос", "short": "Цвет и уход", "description": "Подбираем оттенок и технику под тип волос и желаемый образ.", "priceFrom": "от 3500 ₽"},
        {"title": "Оформление бровей", "short": "Выразительная форма", "description": "Коррекция и оттенок для естественного и гармоничного результата.", "priceFrom": "от 900 ₽"},
        {"title": "Макияж", "short": "Образ под событие", "description": "Дневной, вечерний или праздничный макияж под ваш формат мероприятия.", "priceFrom": "от 2000 ₽"},
        {"title": "Укладка", "short": "Финальный акцент", "description": "Легкая или объемная укладка с учетом длины и структуры волос.", "priceFrom": "от 1800 ₽"},
    ]
    print("⚠️ Использован fallback для услуг")
    return fallback[:count]


def generate_faq(context: Dict[str, Any], services: List[Dict[str, str]], count: int = 5) -> List[Dict[str, str]]:
    print("🔍 Генерируем FAQ...")
    system = "Ты редактор FAQ beauty-сайта. Знаешь реальные возражения клиенток с 2GIS. Возвращай только JSON."
    
    data_block = DATA_BLOCK_TEMPLATE.format(**context)
    
    user = data_block + f"""

## ЗАДАЧА: {count} вопросов-ответов закрывающих реальные возражения.

ТЕМЫ ВОПРОСОВ (из анализа 2GIS, iRecommend, Яндекс.Карт):
1. Стерильность инструментов
2. Сколько держится покрытие
3. Что если результат не понравится
4. Можно ли выбрать конкретного мастера
5. Сколько времени занимает процедура
6. Гарантия на работу
7. Как записаться / перенести — без угроз и штрафов

ПРАВИЛА ОТВЕТОВ:
- Конкретика: не «гарантируем качество» а «скол в первые 7 дней — переделаем бесплатно»
- Язык заботы: не «удержим предоплату» а «если нужно перенести — напиши заранее, найдём время»
- Гарантии формулируй твёрдо: «переделаем», «исправим», «вернём» — никогда «постараемся»
- Запрещено упоминать страх в вопросе если он не задан клиенткой явно.
  Ответ должен закрывать страх фактом, а не повторять его формулировку
- Длина: 1–2 предложения

ЭТАЛОН:
Q: «Инструменты стерильные?»
A: «Металлические — автоклав после каждого клиента. Одноразовые — вскрываем при вас.»

Верни JSON: {{\"faq\":[{{\"q\":\"...\",\"a\":\"...\"}}]}}"""

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
            print(f"✅ FAQ готов: {len(out[:count])}")
            return out[:count]

    fallback = [
        {"q": "Нужна ли предварительная запись?", "a": "Да, предварительная запись помогает выбрать удобное время и мастера без ожидания."},
        {"q": "Сколько длится процедура?", "a": "Длительность зависит от услуги, администратор подскажет точное время при записи."},
        {"q": "Можно ли выбрать мастера?", "a": "Да, при записи можно выбрать мастера по специализации и свободным слотам."},
        {"q": "Как узнать итоговую стоимость?", "a": "Мы заранее согласуем состав услуги и ориентировочную стоимость до начала работы."},
        {"q": "Что взять с собой на первый визит?", "a": "Достаточно описать ожидаемый результат, при желании можно показать фото-референсы."},
    ]
    print("⚠️ Использован fallback для FAQ")
    return fallback[:count]


def filter_reviews(reviews: list) -> list:
    """Фильтрует отзывы: только позитивные, полные, конкретные."""
    if not reviews:
        return []

    print("🔍 Фильтруем отзывы...")
    system = "Ты редактор beauty-сайта. Отбираешь отзывы для публикации. Возвращай только JSON."

    user = f"""Вот список отзывов клиентов: {json.dumps(reviews, ensure_ascii=False)}

ЗАДАЧА: отбери только те которые проходят ВСЕ фильтры:
1. Тональность — только позитивная. Отзыв с «но», «подвела», «к сожалению», «не понравилось», «разочарована» — отклонить.
2. Полнота — отзыв заканчивается на полной мысли. Обрезанные на полуслове или многоточии — отклонить.
3. Конкретность — упоминает услугу, результат или мастера. «Всё хорошо» без деталей — отклонить.

Если прошедших меньше 2 — верни лучшие 2 из всех, обрежь по последней точке.
Верни JSON: {{"reviews":[{{"author":"...","text":"..."}}]}}"""

    try:
        api_key = os.getenv("OPENROUTER_API_KEY")
        payload = {
            "model": settings.openrouter_model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8501",
            "X-Title": "KURSOR Content Curator",
        }
        response = requests.post(settings.openrouter_url, headers=headers, json=payload, timeout=settings.request_timeout)
        response.raise_for_status()
        body = response.json()
        raw = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        # убираем markdown-обёртку если модель её добавила
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:-1])
        data = json.loads(cleaned)
        result = data.get("reviews", [])
        if result:
            print(f"✅ filter_reviews: прошло {len(result)} из {len(reviews)}")
            return result
        else:
            print("⚠️ filter_reviews: модель вернула пустой список — берём первый отзыв")
            return reviews[:1]
    except Exception as e:
        print(f"⚠️ filter_reviews ERROR: {e} — берём первый отзыв")
        return reviews[:1]


async def curate_lead(lead_id: int) -> Optional[Dict[str, Any]]:
    print(f"🔍 Запуск Content Curator для lead_id={lead_id}")

    async with get_async_session() as session:
        result = await session.execute(select(Lead).where(Lead.id == lead_id))
        lead = result.scalar_one_or_none()

    if not lead:
        print(f"❌ Лид с ID={lead_id} не найден")
        return None

    business_name = compact_text(lead.name or f"Lead {lead_id}")
    category = compact_text(lead.category or "other")

    slug = slugify_name(business_name, lead_id)
    
    # Читаем extracted JSON (услуги, FAQ, team с сайта)
    extracted_data = {}
    extracted_path = PROJECT_ROOT / "data" / "extracted" / f"{slug}-{lead_id}.json"
    if extracted_path.exists():
        with open(extracted_path, encoding="utf-8") as f:
            extracted_data = json.load(f)
    
    # Читаем yandex JSON (позиционирование, услуги, рейтинг, адрес)
    yandex_data = {}
    yandex_path = PROJECT_ROOT / "data" / "yandex" / f"{slug}-{lead_id}.json"
    if yandex_path.exists():
        with open(yandex_path, encoding="utf-8") as f:
            yandex_data = json.load(f)

    # Берём отзывы из yandex_data с фильтрацией мусора
    yandex_reviews_list = yandex_data.get("yandex", {}).get("reviews_list", [])
    raw_reviews = [
        r for r in yandex_reviews_list
        if r.get("text") and r.get("author")
        and r.get("author") != "Клиент"
        and len(r.get("text", "")) > 30
        and r.get("text") != r.get("author")
    ]

    print(f"🎯 Найден лид: {business_name} | category={category} | reviews={len(raw_reviews)}")

    # Реальные услуги из двух источников
    extracted_services = extracted_data.get("services", [])
    yandex_services = yandex_data.get("yandex", {}).get("services", [])
    real_services = extracted_services[:10] if extracted_services else yandex_services[:10]
    real_services_str = ", ".join([
        s.get("name") or s.get("title") or str(s)
        for s in real_services if isinstance(s, dict)
    ][:10]) or ""
    
    yandex_prices = {}
    for s in yandex_services:
        name = s.get("name", "")
        price = s.get("price", "")
        # чистим мусорные названия
        if name and price and len(name) > 3 and "варьируется" not in name:
            try:
                price_digits = re.sub(r'[^\d]', '', price)
                if not price_digits or int(price_digits) < 500:
                    continue
            except (ValueError, TypeError):
                continue
            clean_name = name.split("варьируется")[0].strip()
            clean_name = clean_name.replace("ВЫПОЛНЯЕТСЯ НА ЧИСТЫЕ ВЫМЫТЫЕ ВАМИ ВОЛОСЫ", "").strip()
            clean_name = re.sub(r'\d+$', '', clean_name).strip()
            if clean_name:
                yandex_prices[clean_name.lower()] = price

    real_services = [
        s.get("name", "")
        for s in extracted_data.get("serviceCarousel", [])
        if s.get("name")
        and len(s.get("name", "")) < 60
        and not any(x in s.get("name", "") for x in
                    ["📞", "варьируется", "ВЫПОЛНЯЕТСЯ", "344"])
    ]
    
    # Собираем цены из serviceCarousel
    carousel_prices = {}
    for s in extracted_data.get("serviceCarousel", []):
        name = s.get("name", "").strip()
        price = s.get("price", "")
        if name and price:
            price_digits = re.sub(r'[^\d]', '', price)
            try:
                if price_digits and int(price_digits) >= 100:
                    carousel_prices[name.lower()] = price
            except (ValueError, TypeError):
                pass
    
    # Объединяем: carousel_prices + yandex_prices (яндекс приоритетнее)
    merged_prices = {**carousel_prices, **yandex_prices}
    
    # Позиционирование с сайта
    yandex_info = yandex_data.get("yandex", {})
    real_tagline = yandex_info.get("tagline") or yandex_info.get("description") or ""
    real_rating = yandex_info.get("rating") or ""
    real_reviews_count = yandex_info.get("reviews_count") or ""
    real_address = lead.address or yandex_info.get("address") or ""
    real_city = detect_city(real_address)
    real_hours = yandex_info.get("hours") or ""

    # Определяем тип бизнеса (сеть или одиночная студия)
    chain_status = is_chain(lead, yandex_data)
    business_type = "сеть салонов красоты" if chain_status else "студия красоты"
    
    salon_context = {
        "name": business_name,
        "city": real_city,
        "address": real_address,
        "business_type": business_type,
        "tagline": real_tagline,
        "services": real_services_str,
        "rating": real_rating,
        "reviews_count": real_reviews_count,
        "hours": real_hours,
        "category": category,
        "yandex_prices": json.dumps(yandex_prices, ensure_ascii=False),
        "real_services": ", ".join(real_services) if real_services else "",
        "real_prices": json.dumps(merged_prices, ensure_ascii=False),
    }

    about_source = extract_about_source(lead.audit_notes)
    tagline_source = extract_tagline_source(lead.audit_notes)

    tagline = generate_tagline(salon_context, source_tagline=tagline_source)
    about_text = write_about_text(salon_context, source_text=about_source)
    curated_reviews = filter_reviews(raw_reviews[:6])
    # Постобработка: убираем обрезанные и негативные отзывы
    BAD_MARKERS = ["к сожалению", "но спустя", "услышала как он",
                   "подвела", "не понравилось", "разочарована",
                   "но ", "однако "]
    TRUNCATION_MARKERS = ["...", "как он", "но спустя", "услышала как"]

    clean_reviews = []
    for r in curated_reviews:
        text = r.get("text", "")
        has_bad = any(m in text.lower() for m in BAD_MARKERS)
        is_truncated = any(text.strip().endswith(m) or m in text[-30:]
                           for m in TRUNCATION_MARKERS)
        if not has_bad and not is_truncated:
            clean_reviews.append(r)

    # Если после чистки ничего не осталось — не показываем отзывы вообще
    # (лучше пусто чем плохо)
    curated_reviews = clean_reviews
    service_cards = generate_services(salon_context, count=5)
    
    # Используем реальные FAQ из extracted_data если есть
    real_faq = extracted_data.get("faq_accordion", [])
    if real_faq:
        faq_items = [{"q": item.get("q", ""), "a": item.get("a", "")} for item in real_faq[:5]]
        print(f"✅ FAQ из источника: {len(faq_items)}")
    else:
        faq_items = generate_faq(salon_context, service_cards, count=5)

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
    out_path = OUTPUT_DIR / f"{slug}-{lead_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, ensure_ascii=False, indent=2)

    print(f"✅ Curated JSON сохранён: {out_path}")
    return {"lead_id": lead_id, "slug": slug, "output_path": str(out_path), "data": output_payload}


def main() -> None:
    init_env()

    parser = argparse.ArgumentParser(description="Content Curator for Radar leads")
    parser.add_argument("lead_id", type=int, help="Lead ID from SQLite database")
    args = parser.parse_args()

    print("=" * 60)
    print("🚀 Content Curator")
    print(f"🤖 Модель: {settings.openrouter_model}")
    print(f"🌐 OpenRouter: {settings.openrouter_url}")
    print(f"🔑 OPENROUTER_API_KEY: {'yes' if bool(os.getenv('OPENROUTER_API_KEY')) else 'no'}")
    print(f"📁 Output: {OUTPUT_DIR}")
    print("=" * 60)

    result = asyncio.run(curate_lead(args.lead_id))
    if not result:
        print("❌ Курация не выполнена")
        return

    print("\n🏁 Курация завершена успешно")
    print(f"📄 Файл: {result['output_path']}")


if __name__ == "__main__":
    main()
