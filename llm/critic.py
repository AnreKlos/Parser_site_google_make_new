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


async def criticize_lead(lead_id: int) -> Optional[Dict[str, Any]]:
    """Оценивает curated контент через LLM-критика."""
    safe_print(f"🔍 Запуск Gemini Critic для lead_id={lead_id}")

    async with get_async_session() as session:
        result = await session.execute(select(Lead).where(Lead.id == lead_id))
        lead = result.scalar_one_or_none()

    if not lead:
        safe_print(f"❌ Лид с ID={lead_id} не найден")
        return None

    business_name = compact_text(lead.name or f"Lead {lead_id}")
    slug = slugify_name(business_name, lead_id)
    curated_path = Path(__file__).parent / "data" / "curated" / f"{slug}.json"

    if not curated_path.exists():
        safe_print(f"❌ Curated файл не найден: {curated_path}")
        return None

    with open(curated_path, encoding="utf-8") as f:
        curated = json.load(f)

    safe_print(f"🎯 Найден лид: {business_name}")
    safe_print(f"📦 Загружен curated: {curated_path.name}")

    # Формируем prompt для критика
    prompt = f"""Ты строгий критик контента для сайтов салонов красоты.
Оцени качество сгенерированного контента для бизнеса "{business_name}".

Контент для оценки:
- Слоган: {curated.get("meta", {}).get("tagline", "")}
- О нас: {curated.get("about", "")}
- Отзывы: {len(curated.get("reviews", []))} шт.
- FAQ: {len(curated.get("faq", []))} шт.
- Услуги: {len(curated.get("services", []))} шт.

Критерии оценки (1–10):
1. Слоган: лаконичность, уместность, без штампов
2. О нас: информативность, стиль, отсутствие воды
3. Отзывы: разнообразие, эмоциональность, правдоподобие
4. FAQ: полезность, релевантность бизнесу
5. Услуги: полнота описаний, ясность цен

Верни СТРОГО JSON: {{"score": <1-10>, "verdict_summary": "<краткий вердикт на русском>"}}.
verdict_summary — 1-2 предложения, что хорошо/плохо."""

    parsed = call_gemini_json(prompt)
    if not isinstance(parsed, dict):
        safe_print("❌ Critic не вернул валидный JSON")
        return None

    score = parsed.get("score")
    verdict_summary = compact_text(str(parsed.get("verdict_summary", "")))

    if not isinstance(score, (int, float)) or score < 1 or score > 10:
        safe_print(f"⚠️ Некорректный score: {score}, использую fallback")
        score = 5

    safe_print(f"✅ Critic оценка: score={score}, verdict={verdict_summary}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{slug}.json"

    output_payload = {
        "lead_id": lead_id,
        "slug": slug,
        "score": score,
        "verdict_summary": verdict_summary,
        "criticized_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, ensure_ascii=False, indent=2)

    safe_print(f"✅ Critic JSON сохранён: {out_path}")
    return output_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Critic endpoint for curated content")
    parser.add_argument("lead_id", type=int, help="Lead ID")
    args = parser.parse_args()

    result = asyncio.run(criticize_lead(args.lead_id))
    if not result:
        raise SystemExit(1)

    print("=" * 60)
    print("🏁 Critic completed")
    print(f"📄 File: data/critic/{result['slug']}.json")
    print(f"📊 Score: {result['score']}/10")
    print(f"📝 Verdict: {result['verdict_summary']}")


if __name__ == "__main__":
    main()
