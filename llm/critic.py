#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

import google.auth
import google.auth.transport.requests
import requests
from dotenv import load_dotenv
from sqlalchemy import select

from db.database import get_async_session
from db.models import Lead
from utils.text import slugify_name

load_dotenv()

# --- НАСТРОЙКИ CRITIC ---
GEMINI_MODEL = "gemini-2.5-flash-lite"
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
REQUEST_TIMEOUT = 60
MAX_JSON_RETRIES = 2
OUTPUT_DIR = Path(__file__).parent / "data" / "critic"

_vertex_credentials = None
_vertex_auth_req = None


def _get_vertex_url() -> str:
    loc = GOOGLE_CLOUD_LOCATION
    proj = GOOGLE_CLOUD_PROJECT
    return (
        f"https://{loc}-aiplatform.googleapis.com/v1/projects/{proj}"
        f"/locations/{loc}/publishers/google/models/{GEMINI_MODEL}:generateContent"
    )


def _get_bearer_token() -> Optional[str]:
    global _vertex_credentials, _vertex_auth_req
    try:
        if _vertex_credentials is None:
            _vertex_credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            _vertex_auth_req = google.auth.transport.requests.Request()
        if not _vertex_credentials.valid:
            _vertex_credentials.refresh(_vertex_auth_req)
        return _vertex_credentials.token
    except Exception as exc:
        safe_print(f"\u274c Не удалось получить Bearer токен Vertex AI: {exc}")
        return None


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

    return None


def _parse_vertex_text(data: dict, attempt: int, total: int) -> str:
    """Пробует оба пути ответа Vertex AI и логирует структуру при неудаче."""
    candidates = data.get("candidates")
    if candidates:
        raw = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if raw:
            return raw
    predictions = data.get("predictions")
    if predictions:
        raw = predictions[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if raw:
            safe_print(f"\u2139\ufe0f Попытка {attempt}/{total}: использован путь 'predictions' в ответе Vertex AI")
            return raw
    safe_print(
        f"\u26a0\ufe0f Попытка {attempt}/{total}: неожиданная структура ответа Vertex AI: "
        + json.dumps(data)[:400]
    )
    return ""


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def call_gemini_json(prompt: str) -> Optional[Any]:
    """Вызывает Gemini через Vertex AI и возвращает распарсенный JSON с ретраями."""
    if not GOOGLE_CLOUD_PROJECT:
        safe_print("\u274c GOOGLE_CLOUD_PROJECT не найден в окружении")
        return None

    total_attempts = MAX_JSON_RETRIES + 1
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1000},
    }

    for attempt in range(1, total_attempts + 1):
        if attempt > 1:
            time.sleep(2)
        token = _get_bearer_token()
        if not token:
            break
        try:
            response = requests.post(
                _get_vertex_url(),
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code >= 400:
                try:
                    err = response.json().get("error", {})
                    detail = compact_text(
                        f"status={err.get('status', '')}; message={err.get('message', '')}"
                    )
                except ValueError:
                    detail = compact_text(response.text[:500])
                safe_print(
                    f"\u26a0\ufe0f Попытка {attempt}/{total_attempts}: Vertex AI HTTP {response.status_code}. {detail}"
                )
            response.raise_for_status()
            data = response.json()
            raw_text = _parse_vertex_text(data, attempt, total_attempts)
            parsed = extract_json_from_text(raw_text)
            if parsed is not None:
                return parsed
            safe_print(f"\u26a0\ufe0f Попытка {attempt}/{total_attempts}: невалидный JSON от Gemini")
        except requests.exceptions.RequestException as exc:
            safe_print(f"\u26a0\ufe0f Попытка {attempt}/{total_attempts}: ошибка запроса к Vertex AI: {exc}")
        except ValueError as exc:
            safe_print(f"\u26a0\ufe0f Попытка {attempt}/{total_attempts}: ошибка декодирования ответа: {exc}")

    safe_print("\u274c Gemini не вернула валидный JSON после всех попыток")
    return None


async def criticize_lead(lead_id: int) -> Optional[Dict[str, Any]]:
    """Оценивает готовый config.js через LLM-критика."""
    safe_print(f"🔍 Запуск Gemini Critic для lead_id={lead_id}")

    async with get_async_session() as session:
        result = await session.execute(select(Lead).where(Lead.id == lead_id))
        lead = result.scalar_one_or_none()

    if not lead:
        safe_print(f"❌ Лид с ID={lead_id} не найден")
        return None

    business_name = compact_text(lead.name or f"Lead {lead_id}")
    slug = slugify_name(business_name, lead_id)
    
    # Пути к файлам с новым форматом {slug}-{lead_id}
    curated_path = Path(__file__).parent.parent / "data" / "curated" / f"{slug}-{lead_id}.json"
    config_path = Path(r"D:\2 Clode Proj\1\neuralsync\src\configs") / f"{slug}-{lead_id}.config.js"
    
    # Fallback для старого формата
    if not curated_path.exists():
        curated_path = Path(__file__).parent.parent / "data" / "curated" / f"{slug}.json"
    
    if not curated_path.exists():
        safe_print(f"❌ Curated файл не найден: {curated_path}")
        return None

    with open(curated_path, encoding="utf-8") as f:
        curated = json.load(f)
    
    # Загружаем config.js если существует
    config_data = None
    if config_path.exists():
        try:
            # Используем Node.js для парсинга JavaScript (ES6 module syntax)
            import subprocess
            import tempfile
            
            # Создаём временный Node.js скрипт для избежания проблем с escaping
            node_script_content = f"""
const fs = require('fs');
const content = fs.readFileSync('{config_path.as_posix()}', 'utf8');
const match = content.match(/export const (\\w+)Config = (\\{{[\\s\\S]*\\}});/);
if (match) {{
    const obj = eval('(' + match[2] + ')');
    console.log(JSON.stringify(obj));
}}
"""
            with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8') as f:
                f.write(node_script_content)
                temp_script = f.name
            
            try:
                result = subprocess.run(
                    ["node", temp_script],
                    capture_output=True,
                    encoding='utf-8',
                    text=True,
                    timeout=10
                )
                if result.returncode == 0 and result.stdout:
                    config_data = json.loads(result.stdout)
                    safe_print(f"✅ Config.js загружен через Node.js")
                else:
                    safe_print(f"⚠️ Node.js не смог загрузить config: {result.stderr}")
            finally:
                os.unlink(temp_script)
        except Exception as e:
            safe_print(f"⚠️ Не удалось загрузить config.js через Node.js: {e}")

    safe_print(f"🎯 Найден лид: {business_name}")
    safe_print(f"📦 Загружен curated: {curated_path.name}")
    if config_data:
        safe_print(f"📦 Загружен config.js: {config_path.name}")
    else:
        safe_print(f"⚠️ Config.js не найден, оценка только по curated")

    # Формируем sources_used
    sources_used = []
    if curated_path.exists():
        sources_used.append("curated")
    if config_data:
        sources_used.append("config")
    
    # Формируем prompt для критика с оценкой по Карте болей
    prompt = f"""Ты строгий критик лендингов для салонов красоты.
Оцени качество готового сайта для бизнеса "{business_name}".

ИСТОЧНИКИ ДАННЫХ: {", ".join(sources_used)}

Контент для оценки:
- Слоган: {curated.get("meta", {}).get("tagline", "")}
- О нас: {curated.get("about", "")}
- Отзывы: {len(curated.get("reviews", []))} шт.
- FAQ: {len(curated.get("faq", []))} шт.
- Услуги в curated: {len(curated.get("services", []))} шт.
"""

    if config_data:
        hero = config_data.get("hero", {})
        services = config_data.get("sections", {}).get("services", {})
        price = config_data.get("sections", {}).get("price", {})
        gallery = config_data.get("sections", {}).get("gallery", {})
        
        prompt += f"""
ГОТОВЫЙ КОНФИГ:
- Hero: titleLine1="{hero.get("titleLine1", "")}", titleLine1Small="{hero.get("titleLine1Small", "")}"
- Services: enabled={services.get("enabled", False)}, items={len(services.get("items", []))}
- Price: enabled={price.get("enabled", False)}, items={len(price.get("groups", [{}])[0].get("items", [])) if isinstance(price.get("groups"), list) and price.get("groups") else 0}
- Gallery: enabled={gallery.get("enabled", False)}, items={len(gallery.get("items", []))}
- Block flags: {config_data.get("block_flags", {})}
"""

    prompt += """
СТРУКТУРА CONFIG (для понимания данных):

sections.price:
- enabled: boolean
- groups: [{items: [{title, price, description}]}]
- Если enabled=true И groups[0].items.length > 0 → ПРАЙС ЕСТЬ

sections.gallery:
- items: [путь1, путь2, ...]
- Если items.length > 0 → ФОТО ЕСТЬ

sections.services:
- items: [{title, description, priceFrom}]
- Если items.length > 0 → УСЛУГИ ЕСТЬ

hero:
- titleLine1: string
- titleLine1Small: string
- Если titleLine1 И titleLine1Small заполнены → HERO ЕСТЬ

sections.about:
- text: string
- Если text заполнен → ABOUT ЕСТЬ

sections.bookingContacts:
- enabled: boolean
- Если enabled=true → КОНТАКТЫ ЕСТЬ

meta:
- brand: {name, city}
- Если brand.name И brand.city заполнены → META ЕСТЬ

ПРИМЕРЫ ПРАВИЛЬНОЙ ОЦЕНКИ:

Пример 1 (готовый лендинг):
Config:
  sections.price.enabled = true, groups[0].items = [5 услуг]
  sections.gallery.items = [3 фото]
  hero.titleLine1 = "Калинка-Малинка"
  sections.bookingContacts.enabled = true

Оценка:
  verdict: "ready"
  score: 85
  breakdown: {data: 80, price: 90, gallery: 80, content: 80, meta: 90}
  issues: []

Пример 2 (неполные данные):
Config:
  sections.price.enabled = false
  sections.gallery.items = []
  sections.services.items = []

Оценка:
  verdict: "rejected"
  score: 20
  breakdown: {data: 10, price: 0, gallery: 0, content: 30, meta: 50}
  issues: [
    {severity: "critical", block: "price", problem: "Нет прайса с ценами"},
    {severity: "critical", block: "gallery", problem: "Нет фото работ"}
  ]

ИНСТРУКЦИЯ:
Сначала проверь наличие данных в config по структуре выше, потом оценивай качество.

КРИТЕРИИ ОЦЕНКИ (по КАРТЕ БОЛЕЙ владельца):

1. Закрывает ли сайт ТОП-3 боли владельца:
   - "Нет онлайн-записи" — есть ли кнопка записи/контакты?
   - "Нет цен" — есть ли секция price с реальными ценами?
   - "Нет фото работ" — есть ли gallery с фото?

2. Критичные пустоты:
   - Нет цен в price секции (или price отключён)
   - Нет фото в gallery (или gallery отключён)
   - Нет контактов/адреса
   - Услуги без описаний

3. Использование дефолтов:
   - "Моностудия" вместо реального типа бизнеса
   - "вашем городе" вместо реального города
   - Плейсхолдеры вместо реальных данных

4. Мусор и артефакты:
   - Дубли текстов
   - Битые/обрезанные тексты
   - Имена владельцев как услуги ("Анна", "Мария")
   - Склейки брендов/рейтингов ("Империя красоты4,4Стрижка")

5. Качество контента:
   - Слоган: лаконичность, уместность, без штампов
   - О нас: информативность, стиль, отсутствие воды
   - Отзывы: разнообразие, эмоциональность, правдоподобие
   - FAQ: полезность, релевантность бизнесу
   - Услуги: полнота описаний, ясность цен

ВЕРДИКТЫ:
- "ready" — можно показывать клиенту (нет критичных проблем)
- "needs_review" — нужна ручная проверка (есть умеренные проблемы)
- "rejected" — критичные проблемы, показывать нельзя

Верни СТРОГО JSON:
{
  "verdict": "ready" | "needs_review" | "rejected",
  "score": <0-100>,
  "issues": [
    {"severity": "critical" | "moderate" | "minor", "block": "hero|services|price|gallery|about|contacts", "problem": "<описание>"}
  ],
  "scores_breakdown": {
    "data_completeness": <0-100>,
    "price_quality": <0-100>,
    "gallery_quality": <0-100>,
    "content_quality": <0-100>,
    "meta_quality": <0-100>
  },
  "summary": "<краткое резюме на русском, 2-3 предложения>"
}

score — общая оценка качества (0-100).
issues — список найденных проблем (пустой если нет).
scores_breakdown — детализация по категориям.
summary — что хорошо/плохо, что нужно исправить.

КРИТЕРИИ ДЛЯ scores_breakdown.price_quality:

ВЫСОКАЯ ОЦЕНКА (80-100):
- sections.price.enabled = true
- groups[0].items.length >= 3
- У КАЖДОГО item есть непустой price (формат "X ₽" или число)
- Descriptions опциональны (НЕ снижают оценку если цены есть)
- Если descriptions есть - бонус +5-10 баллов

СРЕДНЯЯ ОЦЕНКА (50-70):
- Цены есть, но меньше 3 позиций
- ИЛИ формат цен некорректный (без валюты)
- ИЛИ descriptions низкого качества (но цены есть)

НИЗКАЯ ОЦЕНКА (0-40):
- sections.price.enabled = false
- ИЛИ нет items вообще
- ИЛИ у items нет поля price (null/empty)

ВАЖНО: Цены важнее описаний. Если цены есть и правильные - оценка не ниже 70, даже если descriptions пустые или низкого качества."""

    parsed = call_gemini_json(prompt)
    if not isinstance(parsed, dict):
        safe_print("❌ Critic не вернул валидный JSON")
        return None

    # Парсим новую схему ответа
    verdict = parsed.get("verdict", "needs_review")
    score = parsed.get("score", 50)
    issues = parsed.get("issues", [])
    scores_breakdown = parsed.get("scores_breakdown", {})
    summary = compact_text(str(parsed.get("summary", "")))

    # Валидация verdict
    if verdict not in ["ready", "needs_review", "rejected"]:
        safe_print(f"⚠️ Некорректный verdict: {verdict}, использую fallback")
        verdict = "needs_review"

    # Валидация score
    if not isinstance(score, (int, float)) or score < 0 or score > 100:
        safe_print(f"⚠️ Некорректный score: {score}, использую fallback")
        score = 50

    safe_print(f"✅ Critic оценка: verdict={verdict}, score={score}")
    if issues:
        safe_print(f"   Найдено проблем: {len(issues)}")
        for issue in issues[:3]:  # Показываем первые 3
            safe_print(f"   - [{issue.get('severity')}] {issue.get('block')}: {issue.get('problem')}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{slug}-{lead_id}.json"

    output_payload = {
        "lead_id": lead_id,
        "slug": slug,
        "verdict": verdict,
        "score": score,
        "issues": issues,
        "scores_breakdown": scores_breakdown,
        "summary": summary,
        "sources_used": sources_used,
        "criticized_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, ensure_ascii=False, indent=2)

    safe_print(f"✅ Critic JSON сохранён: {out_path}")
    return output_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Critic endpoint for landing pages")
    parser.add_argument("lead_id", type=int, help="Lead ID")
    args = parser.parse_args()

    result = asyncio.run(criticize_lead(args.lead_id))
    if not result:
        raise SystemExit(1)

    print("=" * 60)
    print("🏁 Critic completed")
    print(f"📄 File: data/critic/{result['slug']}-{result['lead_id']}.json")
    print(f"🎯 Verdict: {result['verdict']}")
    print(f"📊 Score: {result['score']}/100")
    print(f"📝 Summary: {result['summary']}")
    if result.get('scores_breakdown'):
        print(f"📈 Breakdown: {result['scores_breakdown']}")
    if result.get('issues'):
        print(f"⚠️ Issues ({len(result['issues'])}):")
        for issue in result['issues'][:5]:
            print(f"   - [{issue.get('severity')}] {issue.get('block')}: {issue.get('problem')}")


if __name__ == "__main__":
    main()
