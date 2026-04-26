#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Site Config Generator - генерация JSON-конфигурации для сайта клиента через LLM.

Принимает данные лида (название, телефон, сырые тексты, отзывы) и создаёт
структурированный JSON для генерации сайта через site_generator.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import json
import asyncio
import shutil
import logging
from typing import Optional, Dict, Any
from dotenv import load_dotenv
import requests

from db.models import Lead
from db.database import get_async_session

load_dotenv()

def safe_print(text: str) -> None:
    """Безопасный print с защитой от charmap ошибок на Windows."""
    try:
        print(text)
    except (UnicodeEncodeError, UnicodeError):
        print(text.encode('cp1251', errors='replace').decode('cp1251'))


# Настраиваем логгер для CLI
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)

# --- OpenRouter конфигурация ---
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "gpt-4o-mini"

# Отладочный вывод при загрузке модуля
safe_print(f"[DEBUG] OPENROUTER_API_KEY loaded: {bool(OPENROUTER_API_KEY)}")
if OPENROUTER_API_KEY:
    safe_print(f"[DEBUG] Key prefix: {OPENROUTER_API_KEY[:8]}...")

# --- Пути ---
TEMPLATES_JSON_DIR = Path(__file__).parent.parent / "data" / "templates_json"
NEXTJS_TEMPLATE_PATH = Path(__file__).parent.parent.parent / "12 TEMPLATE" / "src" / "data" / "client_data.json"


# =====================================================================
# СИСТЕМНЫЙ ПРОМПТ
# =====================================================================

SYSTEM_PROMPT_SITE_JSON = """Ты — генератор конфигурации для сайта медицинской клиники.

На вход тебе даются сырые данные, собранные с сайта клиники и из Google Maps:
- Название клиники
- Телефон
- Адрес
- Услуги (список)
- Отзывы клиентов
- Дополнительная информация о компании

Твоя задача — создать СТРОГО валидный JSON следующей структуры:

{
  "clinic": {
    "name": "Полное название клиники",
    "phone": "Основной телефон",
    "address": "Полный адрес"
  },
  "hero": {
    "title": "Маркетинговый заголовок (5-8 слов, цепляющий)",
    "subtitle": "Убедительный подзаголовок (1-2 предложения, раскрывает ценность)",
    "cta_text": "Текст кнопки действия (например 'Записаться на приём')"
  },
  "services": [
    {
      "id": "1",
      "title": "Название услуги",
      "price": "от XXX руб",
      "icon": "🦷"
    }
  ],
  "testimonials": [
    {
      "id": "1",
      "name": "Имя клиента",
      "text": "Полный текст отзыва",
      "rating": 5
    }
  ],
  "ai_settings": {
    "enabled": true,
    "bot_name": "Дентал-Бот",
    "welcome_message": "Приветственное сообщение чат-бота",
    "system_prompt": "Системный промпт для ИИ-ассистента клиники"
  }
}

КРИТИЧЕСКИ ВАЖНО:
1. Ответ должен быть ТОЛЬКО валидный JSON, без markdown, без пояснений
2. Все поля ОБЯЗАНЫ присутствовать (никаких null)
3. services — минимум 3 услуги, максимум 8. Если в данных больше — выбери самые важные
4. testimonials — используй РЕАЛЬНЫЕ отзывы из данных. НЕ выдумывай! Если отзывов нет — оставь пустой массив []
5. rating — число от 1 до 5
6. icon — используй эмодзи, подходящий к услуге (🦷💉👁️🧴💊🏥🩺🔬🧬💆‍♀️)
7. title в hero — должен быть цепляющим, маркетинговым, на русском языке
8. subtitle — должен раскрывать ценность клиники
9. ai_settings.system_prompt — должен содержать инструкцию для бота и ВСЕ услуги из массива services
10. ai_settings.welcome_message — дружелюбное приветствие от имени клиники

Пиши на РУССКОМ языке. Все тексты должны быть готовы для публикации на сайте."""


# =====================================================================
# ФУНКЦИЯ ПОДГОТОВКИ ПРОМПТА
# =====================================================================

def build_site_gen_prompt(lead: Lead, scraped_data: Optional[Dict[str, Any]] = None) -> str:
    """
    Формирует user-промпт для генерации JSON сайта.
    
    Args:
        lead: Модель Lead из БД
        scraped_data: Дополнительные данные скрейпинга (если есть)
    """
    name = lead.name or "Неизвестная клиника"
    phone = lead.phone or ""
    address = lead.address or ""
    website = lead.website or ""
    
    # Собираем услуги из разных источников
    services_text = ""
    if scraped_data and scraped_data.get("services"):
        services_list = scraped_data["services"]
        if isinstance(services_list, list):
            svc_lines = []
            for svc in services_list[:15]:  # Ограничиваем
                svc_name = svc.get("name", "") if isinstance(svc, dict) else str(svc)
                svc_price = svc.get("price_from", "") if isinstance(svc, dict) else ""
                line = f"  - {svc_name}"
                if svc_price:
                    line += f" (цена: {svc_price})"
                svc_lines.append(line)
            services_text = "\n".join(svc_lines)
    
    if not services_text and scraped_data and scraped_data.get("data", {}).get("services"):
        # Fallback на старый формат
        raw_services = scraped_data["data"]["services"]
        if isinstance(raw_services, list):
            services_text = "\n".join([f"  - {s}" for s in raw_services[:15]])
    
    # Собираем отзывы
    reviews_text = ""
    if lead.raw_reviews:
        try:
            reviews = json.loads(lead.raw_reviews)
            if isinstance(reviews, list):
                rev_lines = []
                for rev in reviews[:10]:
                    rev_name = rev.get("author", rev.get("name", "Клиент"))
                    rev_text = rev.get("text", "")[:300]
                    rev_rating = rev.get("rating", 5)
                    rev_lines.append(f'  - {rev_name} ({rev_rating}★): "{rev_text}"')
                reviews_text = "\n".join(rev_lines)
        except (json.JSONDecodeError, TypeError):
            reviews_text = lead.raw_reviews[:500]
    
    # Дополнительная информация
    extra_info = ""
    if lead.audit_notes:
        extra_info += f"- Примечания аудита: {lead.audit_notes}\n"
    if scraped_data:
        normalized = scraped_data.get("normalized", {})
        if normalized.get("tagline"):
            extra_info += f"- Слоган: {normalized['tagline']}\n"
        if normalized.get("about"):
            extra_info += f"- О компании: {normalized['about']}\n"
        if normalized.get("benefits"):
            benefits = normalized["benefits"]
            if isinstance(benefits, list):
                extra_info += f"- Преимущества: {', '.join(str(b) for b in benefits[:5])}\n"

    return f"""Создай JSON-конфигурацию для сайта следующей клиники:

Название: {name}
Телефон: {phone}
Адрес: {address}
Сайт: {website}

{f"Услуги (из данных скрейпинга):\n{services_text}" if services_text else "Услуги: НЕИЗВЕСТНО (придумай типичные для медицинской клиники)"}

{f"Отзывы клиентов (ИСПОЛЬЗУЙ ТОЛЬКО ЭТИ РЕАЛЬНЫЕ ОТЗЫВЫ):\n{reviews_text}" if reviews_text else "Отзывы: НЕ НАЙДЕНЫ (оставь массив testimonials пустым [])"}

{f"Дополнительная информация:\n{extra_info}" if extra_info else ""}

ВАЖНО:
- Если услуги не найдены — создай 5-7 типичных услуг для медицинской клиники
- Используй РЕАЛЬНЫЕ отзывы, НЕ выдумывай новые
- Все тексты на РУССКОМ языке
- JSON должен быть ВАЛИДНЫМ (проверь кавычки, запятые, скобки)

Создай ТОЛЬКО JSON, без пояснений:"""


# =====================================================================
# ВЫЗОВ LLM
# =====================================================================

def call_llm_for_json(user_prompt: str) -> Optional[Dict[str, Any]]:
    """
    Вызывает OpenRouter API для генерации JSON конфигурации сайта.

    Returns:
        Распарсенный dict или None при ошибке
    """
    if not OPENROUTER_API_KEY:
        safe_print("[ERR] OPENROUTER_API_KEY not set in environment")
        raise ValueError("OPENROUTER_API_KEY не найден в переменных окружения. Проверь файл .env")

    response = None
    try:
        safe_print(f"[API] Calling OpenRouter: model={OPENROUTER_MODEL}")
        
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8501",  # Обязательно для OpenRouter
            "X-Title": "Kursor Dashboard"  # Обязательно для OpenRouter
        }
        
        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT_SITE_JSON},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 4000
            },
            timeout=120
        )
        response.raise_for_status()
        result = response.json()
        content = result['choices'][0]['message']['content']

        # Clean markdown code blocks
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0].strip()
        elif '```' in content:
            content = content.split('```')[1].split('```')[0].strip()

        data = json.loads(content)
        safe_print(f"[OK] Site config generation successful")
        return data

    except json.JSONDecodeError as e:
        safe_print(f"[ERR] Invalid JSON from LLM: {e}")
        safe_print(f"Raw content: {content[:500]}")
        return None
    except requests.exceptions.HTTPError as e:
        safe_print(f"[ERR] HTTP error: {e}")
        if response is not None:
            safe_print(f"[ERR] Status code: {response.status_code}")
            safe_print(f"[ERR] Response: {response.text[:500]}")
        return None
    except Exception as e:
        safe_print(f"[ERR] LLM call failed: {type(e).__name__}: {e}")
        return None


# =====================================================================
# СОХРАНЕНИЕ JSON
# =====================================================================

def save_site_config(config: Dict[str, Any], domain: str) -> str:
    """
    Сохраняет JSON конфигурацию сайта в файл И копирует в шаблон Next.js.

    Args:
        config: Сгенерированный JSON
        domain: Доменное имя (для имени файла)

    Returns:
        Путь к сохранённому файлу (templates_json)
    """
    TEMPLATES_JSON_DIR.mkdir(parents=True, exist_ok=True)

    # Санитизация имени файла
    safe_domain = domain.replace("/", "_").replace("\\", "_").replace(":", "_")
    if not safe_domain:
        safe_domain = "unknown"

    file_path = TEMPLATES_JSON_DIR / f"{safe_domain}.json"

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    safe_print(f"[OK] Site config saved to {file_path}")

    # === АВТО-КОПИРОВАНИЕ В ШАБЛОН NEXT.JS ===
    try:
        if NEXTJS_TEMPLATE_PATH.parent.exists():
            # Записываем тот же JSON напрямую в client_data.json
            NEXTJS_TEMPLATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(NEXTJS_TEMPLATE_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

            safe_print(f"[OK] JSON автоматически выгружен в папку 12 TEMPLATE: {NEXTJS_TEMPLATE_PATH}")
        else:
            safe_print(f"[WARN] Папка шаблона Next.js не найдена: {NEXTJS_TEMPLATE_PATH.parent}")
            safe_print(f"[WARN] Скопируйте {file_path} в {NEXTJS_TEMPLATE_PATH} вручную")
    except Exception as e:
        safe_print(f"[WARN] Не удалось скопировать JSON в шаблон Next.js: {e}")
        safe_print(f"[WARN] Основной файл сохранён: {file_path}")
        safe_print(f"[WARN] Скопируйте вручную в {NEXTJS_TEMPLATE_PATH}")

    return str(file_path)


# =====================================================================
# ГЛАВНЫЕ ФУНКЦИИ ГЕНЕРАЦИИ
# =====================================================================

async def generate_site_config_async(lead_id: int, scraped_data: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    """
    Генерирует JSON-конфигурацию сайта для одного лида.
    
    Args:
        lead_id: ID лида в БД
        scraped_data: Данные скрейпинга (если есть)
        
    Returns:
        dict с lead_id, name, file_path, config или None при ошибке
    """
    from sqlalchemy import select, update

    async with get_async_session() as session:
        result = await session.execute(
            select(Lead).where(Lead.id == lead_id)
        )
        lead = result.scalar_one_or_none()

        if not lead:
            safe_print(f"[ERR] Lead with ID={lead_id} not found")
            return None

        domain = lead.website or f"lead_{lead_id}"
        # Извлекаем домен из URL
        if "://" in domain:
            domain = domain.split("://")[1]
        domain = domain.rstrip("/").split("/")[0]

        safe_print(f"[INFO] Generating site config for: {lead.name} (domain: {domain})")

        user_prompt = build_site_gen_prompt(lead, scraped_data)
        config = call_llm_for_json(user_prompt)

        if config:
            # Добавляем theme на основе категории лида
            category = lead.category or "other"
            config["theme"] = category
            
            file_path = save_site_config(config, domain)
            
            # Сохраняем путь к JSON в БД
            await session.execute(
                update(Lead)
                .where(Lead.id == lead.id)
                .values(site_config_path=file_path)
            )
            await session.commit()

            safe_print(f"[OK] Site config generated for {lead.name}: {file_path}")

            return {
                "lead_id": lead.id,
                "name": lead.name,
                "domain": domain,
                "file_path": file_path,
                "config": config
            }
        else:
            safe_print(f"[ERR] Failed to generate site config for {lead.name}")
            return None


def generate_site_config(lead_id: int, scraped_data: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    """Синхронная обёртка для генерации JSON сайта"""
    return asyncio.run(generate_site_config_async(lead_id, scraped_data))


def generate_site_configs_sync(lead_ids: list, scraped_data_map: Optional[Dict[int, Dict]] = None) -> dict:
    """
    Генерирует JSON конфигурации для нескольких лидов.
    
    Args:
        lead_ids: Список ID лидов
        scraped_data_map: dict {lead_id: scraped_data}
        
    Returns:
        dict со статистикой и результатами
    """
    stats = {
        "total": len(lead_ids),
        "generated": 0,
        "errors": 0,
        "results": []
    }

    for lead_id in lead_ids:
        scraped_data = scraped_data_map.get(lead_id) if scraped_data_map else None
        result = generate_site_config(lead_id, scraped_data)
        
        if result:
            stats["generated"] += 1
            stats["results"].append(result)
        else:
            stats["errors"] += 1

    return stats


# --- CLI запуск ---
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate site config JSON for a lead")
    parser.add_argument("lead_id", type=int, help="Lead ID from database")
    args = parser.parse_args()
    
    print(f"[START] Site Config Generator for Lead ID={args.lead_id}")
    print("=" * 55)
    print(f"  API: {OPENROUTER_URL}")
    print(f"  Модель: {OPENROUTER_MODEL}")
    key_status = "[OK] установлен" if OPENROUTER_API_KEY else "[X] НЕ НАЙДЕН"
    print(f"  Ключ: {key_status}")
    print(f"  Output: {TEMPLATES_JSON_DIR}")
    print("=" * 55)

    result = generate_site_config(args.lead_id)

    if result:
        print(f"\n[OK] Site config generated:")
        print(f"  Lead: {result['name']} (ID={result['lead_id']})")
        print(f"  Domain: {result['domain']}")
        print(f"  File: {result['file_path']}")
        print(f"\n[JSON Preview]")
        print(json.dumps(result['config'], ensure_ascii=False, indent=2)[:1000])
    else:
        print("\n[ERR] Failed to generate site config")
