#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import base64
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import google.auth
import google.auth.transport.requests
import requests
from dotenv import load_dotenv
from sqlalchemy import select

from db.database import get_async_session
from db.models import Lead

load_dotenv()


# --- НАСТРОЙКИ CURATOR ---
GEMINI_MODEL = "gemini-2.5-flash-lite"
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
REQUEST_TIMEOUT = 60
MAX_JSON_RETRIES = 2
OUTPUT_DIR = Path(__file__).parent / "data" / "curated"

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


def call_gemini_json(prompt: str) -> Optional[Any]:
    """Вызывает Gemini через Vertex AI и возвращает распарсенный JSON с ретраями."""
    if not GOOGLE_CLOUD_PROJECT:
        safe_print("\u274c GOOGLE_CLOUD_PROJECT не найден в окружении")
        return None

    total_attempts = MAX_JSON_RETRIES + 1
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2000},
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


def analyze_image_url(image_url: str, prompt: str) -> Optional[Any]:
    """Скачивает изображение по URL и отправляет его в Gemini (Vertex AI) как inlineData."""
    if not GOOGLE_CLOUD_PROJECT:
        safe_print("\u274c GOOGLE_CLOUD_PROJECT не найден в окружении")
        return None

    if not image_url:
        return None

    try:
        image_resp = requests.get(image_url, timeout=15)
        image_resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        safe_print(f"\u26a0\ufe0f Не удалось скачать изображение: {exc}")
        return None

    content_type = (image_resp.headers.get("Content-Type") or "").split(";")[0].strip()
    mime_type = content_type if content_type.startswith("image/") else ""
    if not mime_type:
        guessed, _ = mimetypes.guess_type(image_url)
        mime_type = guessed if guessed and guessed.startswith("image/") else "image/jpeg"

    image_b64 = base64.b64encode(image_resp.content).decode()

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": mime_type, "data": image_b64}},
                ],
            }
        ],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1000},
    }

    total_attempts = MAX_JSON_RETRIES + 1
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
                    f"\u26a0\ufe0f Попытка {attempt}/{total_attempts}: Vertex AI HTTP {response.status_code} (image). {detail}"
                )
            response.raise_for_status()
            data = response.json()
            raw_text = _parse_vertex_text(data, attempt, total_attempts)
            parsed = extract_json_from_text(raw_text)
            if parsed is not None:
                return parsed
            safe_print(f"\u26a0\ufe0f Попытка {attempt}/{total_attempts}: невалидный JSON от Gemini (image)")
        except requests.exceptions.RequestException as exc:
            safe_print(f"\u26a0\ufe0f Попытка {attempt}/{total_attempts}: ошибка запроса к Vertex AI (image): {exc}")
        except ValueError as exc:
            safe_print(f"\u26a0\ufe0f Попытка {attempt}/{total_attempts}: ошибка декодирования ответа (image): {exc}")

    safe_print("\u274c Gemini не вернула валидный JSON по изображению")
    return None


def _validate_rich_profile(data: Any) -> Dict[str, Any]:
    """Defensive validation of rich profile from Vision with new fields."""
    if not isinstance(data, dict):
        return {"rejected": True, "reject_reason": "parse_error", "fit_scores": {
            "hero": {"score": 0, "reason": "parse_error"},
            "about": {"score": 0, "reason": "parse_error"},
            "gallery": {"score": 0, "reason": "parse_error"},
            "team": {"score": 0, "reason": "parse_error"},
            "services": {"score": 0, "reason": "parse_error"},
        }}

    # Ensure fit_scores exists with all 5 roles
    fit_scores = data.get("fit_scores") or {}
    for role in ("hero", "about", "gallery", "team", "services"):
        if role not in fit_scores or not isinstance(fit_scores[role], dict):
            fit_scores[role] = {"score": 0, "reason": "missing"}
        else:
            fit_scores[role].setdefault("score", 0)
            fit_scores[role].setdefault("reason", "")
    data["fit_scores"] = fit_scores

    # Ensure text_in_image exists
    text_in_image = data.get("text_in_image") or {}
    text_in_image.setdefault("present", False)
    text_in_image.setdefault("looks_like_caption_for_person", False)
    text_in_image.setdefault("extracted_text", None)
    data["text_in_image"] = text_in_image

    # Ensure people block has new fields
    people = data.get("people") or {}
    people.setdefault("present", False)
    people.setdefault("count", 0)
    people.setdefault("faces_visible", False)
    people.setdefault("primary_subject", None)
    people.setdefault("back_or_side_only", False)
    people.setdefault("covered_face", False)
    data["people"] = people

    # Ensure flags block
    flags = data.get("flags") or {}
    for f in ("has_logo", "has_text_overlay", "has_watermark", "is_screenshot",
              "face_covered", "low_resolution", "stock_photo_signals"):
        flags.setdefault(f, False)

    # Backward compatibility: if old "medical_mask" exists, fold into face_covered
    if (data.get("flags") or {}).get("medical_mask") and not flags.get("face_covered"):
        flags["face_covered"] = True
    data["flags"] = flags

    # Ensure marketing_grade exists with defaults (Patch B)
    mg = data.get("marketing_grade") or {}
    mg.setdefault("score", 5)         # neutral fallback
    mg.setdefault("tier", "casual_acceptable")
    mg.setdefault("reasons", [])
    data["marketing_grade"] = mg

    # Ensure content
    content = data.get("content") or {}
    content.setdefault("description", "")
    content.setdefault("alt_text", "")
    content.setdefault("objects", [])
    content.setdefault("dominant_colors", [])
    content.setdefault("mood", "neutral")
    data["content"] = content

    # Defaults
    data.setdefault("rejected", False)
    data.setdefault("reject_reason", None)
    data.setdefault("category", "other")
    data.setdefault("service_ref", None)

    # Quality block
    quality = data.get("quality") or {}
    quality.setdefault("score", 5)
    quality.setdefault("sharpness", 5)
    quality.setdefault("lighting", 5)
    quality.setdefault("composition", 5)
    data["quality"] = quality

    return data


def analyze_photo_rich(image_url: str, hint: str = None) -> Dict[str, Any]:
    """
    Returns rich photo profile WITH role-specific fit scores.
    Hint = Yandex aspect tag (e.g. "Интерьер", "Маникюр") — bias only, not authority.
    """
    hint_text = (
        f"\nYandex auto-tagged this photo as: '{hint}'. Use as a hint, "
        f"but YOU decide the final category based on what you see."
        if hint else ""
    )

    prompt = f"""You are a beauty-salon landing page editor. Analyze this photo and decide HOW WELL it fits each landing-page role.{hint_text}

Return STRICT JSON (no markdown, no comments):

{{
  "rejected": true|false,
  "reject_reason": null | "stock_photo" | "face_covered" | "low_quality" | "watermark" | "screenshot" | "text_overlay_heavy" | "ui_interface" | "irrelevant" | "blurry" | "duplicate_pattern",

  "category": "interior" | "work_result" | "master_at_work" | "team_portrait" | "exterior" | "service_card" | "logo" | "tools_products" | "selfie" | "other",

  "quality": {{
    "score": 1-10,
    "sharpness": 1-10,
    "lighting": 1-10,
    "composition": 1-10
  }},

  "marketing_grade": {{
    "score": 1-10,
    "tier": "pro_studio" | "pro_salon" | "casual_acceptable" | "amateur_outdoor" | "amateur_home" | "unusable",
    "reasons": ["short_reason_1", "short_reason_2"]
  }},

  "content": {{
    "description": "1-2 sentence Russian description of what's in the photo",
    "alt_text": "short Russian alt for HTML (max 80 chars), describes service+result",
    "objects": ["object1", "object2", "object3"],
    "dominant_colors": ["color1", "color2"],
    "mood": "calm" | "energetic" | "luxurious" | "cozy" | "clinical" | "neutral"
  }},

  "people": {{
    "present": true|false,
    "count": 0,
    "faces_visible": true|false,
    "primary_subject": null | "client" | "master" | "team_group" | "model" | "unclear",
    "back_or_side_only": true|false,
    "covered_face": true|false
  }},

  "text_in_image": {{
    "present": true|false,
    "looks_like_caption_for_person": true|false,
    "extracted_text": null | "string with text from photo (name/role if any)"
  }},

  "service_ref": null | "manicure" | "pedicure" | "haircut" | "coloring" | "lashes" | "brows" | "makeup" | "facial" | "hair_treatment" | "nails_general" | "other",

  "fit_scores": {{
    "hero":     {{"score": 0-10, "reason": "1 sentence why"}},
    "about":    {{"score": 0-10, "reason": "1 sentence why"}},
    "gallery":  {{"score": 0-10, "reason": "1 sentence why"}},
    "team":     {{"score": 0-10, "reason": "1 sentence why"}},
    "services": {{"score": 0-10, "reason": "1 sentence why"}}
  }},

  "flags": {{
    "has_logo": true|false,
    "has_text_overlay": true|false,
    "has_watermark": true|false,
    "is_screenshot": true|false,
    "face_covered": true|false,
    "low_resolution": true|false,
    "stock_photo_signals": true|false
  }}
}}

============================================================
HARD REJECT (set rejected=true) if ANY of these is true:
- ANY face-covering object on a person:
    medical/surgical mask, cloth mask, fabric mask of any color,
    respirator, balaclava, scarf covering nose/mouth, neck gaiter
    pulled up over face. If a person's nose AND mouth are both
    obscured by an object, set rejected=true with
    reject_reason="face_covered".
- Watermark, signature, or stock-photo provider mark visible
- Screenshot of phone/computer UI: any of these visible — "HDR" badge, battery indicator, clock/time in corner, signal bars, app icons, status bar, "1x"/"2x" zoom indicator, camera mode labels, recording dot, photo gallery thumbnails. If you see any device interface element overlaid on the photo, set rejected=true with reject_reason="screenshot".
- Heavy text overlay covering >30% of image
- Blurry, dark, severely underexposed
- Pure selfie of a face with no salon context
- Stock-photo signals: airbrushed model, white teeth, generic studio backdrop, towel-on-head cliche, cucumber-on-eyes cliche
- marketing_grade <= 1 (unusable):
    ANY of: новая мебель в полиэтиленовой плёнке, монтаж/стройка/ремонт в кадре,
    мусор/грязь, посторонние личные предметы (телефон, кошелёк, бутылки),
    постельное бельё в кадре, поздравительные надписи во весь кадр.
    Set rejected=true with reject_reason="not_marketable".

============================================================
ROLE-SPECIFIC FIT SCORING — score 0-10 PER ROLE based on these criteria:

HERO (the very first photo a visitor sees, must hook in 3 seconds):
  10 = master in action with visible face/hands + clear visual story (e.g. coloring hair, drawing brow, nail art close-up with hands of master)
   8 = beautiful interior with a person in frame OR striking result close-up (manicure macro, hair after coloring) with composition that draws eye
   6 = clean stylish interior, no people, but good light and depth
   4 = generic interior shot, slightly cluttered or flat
   2 = back of person, back of head, person walking away, no face/no action
   0 = anything that "doesn't tell a story in 3 seconds"

  HARD ZERO if: person shown only from back or side, face not visible AT ALL, only logo or signage, screenshot, dark/blurry

ABOUT (interior / atmosphere, "what kind of place is this"):
  10 = clean, well-lit interior shot of the salon space (chairs, mirrors, work area visible), no close-up procedures
   8 = same, with optional distant master/client visible (sets atmosphere)
   6 = corner/detail of interior (welcome desk, single chair) — usable but secondary
   4 = close-up of work surface (table with tools) — only if no broader shots available
   2 = procedure happening in frame (master + client closeup) — wrong role
   0 = no salon space visible at all

  HARD ZERO if: it's actually a portrait, work result close-up, or master-at-work shot

GALLERY (showcase of WORK RESULTS, before/after style, what client will get):
  10 = sharp close-up of finished work — manicure with detail visible, hair after coloring, brows after correction, lashes after extension
   8 = result shot with hands/face area but the WORK is the subject
   6 = before/after pair OR work-in-progress where the work itself dominates frame
   4 = master-at-work shot where you can see the result forming
   2 = full salon interior, no specific work showcased
   0 = no beauty service result visible

  HARD ZERO if: it's just an interior shot, just a portrait, or a logo

TEAM (master portraits — strict gates):
  10 = single person portrait, face fully visible, professional pose, AND text_in_image.looks_like_caption_for_person == true (caption with name/role on the photo itself)
   8 = single person portrait, face fully visible, professional pose, in clearly recognizable salon setting
   6 = single person portrait, face visible but unclear if it's a master or client
   4 = group photo of 2-3 people, all faces visible, looks like staff
   2 = person shown but face not clearly visible OR back/side only OR multiple people in disorganized scene
   0 = no person, or face hidden by hair/object/angle

  HARD ZERO if: any of these — face not fully visible, person from back, face covered by hair, multiple people without clear "team" framing, person looks like a client (mid-procedure), child in frame

SERVICES (one photo per service card):
  10 = clean shot of the procedure or result of a SPECIFIC service (manicure macro, hair coloring tool in hand, brow shape close-up)
   8 = product/tool arrangement clearly representing a service category
   6 = atmospheric interior shot of the service zone (manicure desk, hair-wash chair)
   4 = generic salon photo
   0 = irrelevant to any service

  HARD ZERO if: it's a portrait of a person without a service context

============================================================
MARKETING GRADE — оценка пригодности для лендинга, отдельно от технического quality.

Это НЕ о резкости/свете. Это о том, можно ли это фото показать клиенту на сайте салона
без потери доверия.

Шкала 1-10 + tier:

10 — pro_studio:
  Студийное освещение. Чистый/нейтральный фон. Композиция выстроена. Услуга — главный субъект.
  Никаких бытовых деталей. Подходит для любого блока лендинга включая hero.

8-9 — pro_salon:
  Снято в салоне на хорошую камеру/телефон с хорошим светом. Видна профессиональная среда
  (рабочее место, оборудование, продукты). Композиция продуманная. Никаких бытовых конфликтов.

6-7 — casual_acceptable:
  Снято в салоне или на природе. Освещение норм, фон нейтральный. Не идеально, но клиента
  не оттолкнёт. Нет бытовых деталей которые «убивают» доверие.

4-5 — amateur_outdoor:
  Снято в неподходящем месте (улица, парковка, дома) но без явных косяков. Может работать
  как backup, не основной выбор.

2-3 — amateur_home:
  Бытовая обстановка: диван, постель, полотенце вместо профессионального белья, личные вещи
  в кадре, домашний свет. Видны непрофессиональные детали (мозоли, неровная кутикула,
  непрорабоанные ногти, морщины крупным планом без ретуши). Фото с ощущением «снято на
  телефон после процедуры для отчётности», а не для маркетинга.

1 — unusable:
  Полиэтиленовая плёнка на новой мебели (товар не распакован). Стройка, монтаж, мусор в кадре.
  Личные предметы случайно попали в кадр (кошельки, телефоны, бутылки). Постельное бельё.
  Грязный пол. Любые «не маркетинговые» детали которые портят образ салона.

ОБЯЗАТЕЛЬНО проставь tier из списка выше. Не выдумывай свои тиры.
В reasons укажи 1-2 короткие причины (через подчёркивание, без пробелов):
  primer: ["plastic_wrap_on_chair", "unfinished_setup"]
  primer: ["bare_feet_on_towel", "home_setting"]
  primer: ["clean_lighting", "neutral_background"]
  primer: ["studio_quality", "perfect_composition"]

============================================================
DEDUPLICATION HINT (output only, used downstream):

Fill content.objects with 3-7 most prominent objects in the photo. Be specific:
  GOOD: ["manicure_desk", "uv_lamp", "client_hands", "nail_polish_bottles"]
  BAD:  ["table", "items", "stuff"]

Fill content.dominant_colors with 2-3 main colors:
  GOOD: ["beige", "white", "rose_gold"]

This lets the downstream code detect near-duplicate photos (same desk, same angle).

============================================================
TEXT-IN-IMAGE DETECTION (critical for team gate):

If you see ANY text/caption on the photo (name, job title, watermark, logo with text):
- text_in_image.present = true
- text_in_image.extracted_text = the text you see (max 100 chars)
- If the text appears to be a person's name AND/OR a job role (мастер, стилист, бровист, etc.) attached to a person in the photo → text_in_image.looks_like_caption_for_person = true

If no text → all three text_in_image fields are false/null.

This is the GATE for team-photo acceptance.

============================================================
Return ONLY the JSON. No prose, no markdown, no explanation outside the JSON.
"""
    parsed = analyze_image_url(image_url, prompt)
    return _validate_rich_profile(parsed)


def detect_ui_overlay(image_url: str) -> Dict[str, Any]:
    """
    Tight Vision call: does the image have ANY device UI element overlaid?
    Returns:
        {
            "has_ui_overlay": bool,
            "evidence": str,   # what was seen, e.g. "HDR badge top-left"
        }
    Use ONLY for finalists (after main fit_score sort) to avoid waste.
    """
    prompt = """Look ONLY at the four corners and the top/bottom edges of this image. Ignore the main subject.

Is there ANY device-interface element overlaid on the photo?
Examples that MUST trigger has_ui_overlay=true:
- "HDR" badge or label
- "Live" badge
- Battery percentage indicator
- Clock / time display
- Signal bars / wifi / cellular icons
- "1x", "0.5x", "2x" zoom indicators
- Camera mode labels ("PHOTO", "VIDEO", "PORTRAIT")
- Recording dot (red circle)
- Screenshot framing
- App UI chrome (status bar, navigation buttons)

Return STRICT JSON, no markdown:
{
  "has_ui_overlay": true|false,
  "evidence": "1 short sentence describing what you see, or null"
}

If you see only photo content with no overlays — has_ui_overlay=false.
"""
    parsed = analyze_image_url(image_url, prompt)
    if not isinstance(parsed, dict):
        return {"has_ui_overlay": False, "evidence": "parse_error"}
    return {
        "has_ui_overlay": bool(parsed.get("has_ui_overlay", False)),
        "evidence": parsed.get("evidence") or "",
    }


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

    parsed = call_gemini_json(prompt)
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

    parsed = call_gemini_json(prompt)
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

    parsed = call_gemini_json(prompt)
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

    parsed = call_gemini_json(prompt)
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

    parsed = call_gemini_json(prompt)
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

    parsed = call_gemini_json(prompt)
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

    parsed = call_gemini_json(prompt)
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
    safe_print(f"🔍 Запуск Gemini Curator для lead_id={lead_id}")

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
    slug = slugify_name(business_name, lead_id)

    safe_print(f"🎯 Найден лид: {business_name} | category={category} | reviews={len(raw_reviews)}")

    # --- Загрузка extracted данных (спарсенных с сайта) ---
    extracted_path = Path(__file__).parent / "data" / "extracted" / f"{slug}.json"
    extracted: Dict[str, Any] = {}
    if extracted_path.exists():
        try:
            with open(extracted_path, encoding="utf-8") as f:
                extracted = json.load(f)
            safe_print(f"📦 Загружены extracted данные: {extracted_path.name}")
        except Exception as exc:
            safe_print(f"⚠️ Не удалось загрузить extracted файл: {exc}")

    # audit_notes используется как внутренний контекст для Gemma
    about_source = extract_about_source(lead.audit_notes)
    tagline_source = extract_tagline_source(lead.audit_notes)

    # --- Услуги: extracted или LLM ---
    extracted_carousel = [item for item in (extracted.get("serviceCarousel") or []) if item.get("name")]
    if extracted_carousel:
        services_seed = [
            {
                "title": compact_text(item["name"]),
                "priceFrom": compact_text(item.get("price", "")) or "от 1500 ₽",
            }
            for item in extracted_carousel
        ]
        safe_print(f"📦 Услуги из extracted: {len(services_seed)} шт.")
    else:
        services_seed = generate_services_seed(category, business_name, city, count=5)
    service_titles = [s["title"] for s in services_seed]

    # --- Отзывы: extracted или DB ---
    extracted_reviews = [r for r in (extracted.get("reviews") or []) if r.get("text")]
    if extracted_reviews:
        raw_reviews = [
            {"author": compact_text(r.get("author", "")), "text": compact_text(r["text"])}
            for r in extracted_reviews
        ]
        safe_print(f"📦 Отзывы из extracted: {len(raw_reviews)} шт.")

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
    safe_print("🚀 Gemini Content Curator")
    safe_print(f"🤖 Модель: {GEMINI_MODEL}")
    safe_print("🌐 Gemini API: generativelanguage.googleapis.com")
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
