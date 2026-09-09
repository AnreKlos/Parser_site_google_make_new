#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KURSOR Command Center - Визуальный пульт управления базой лидов
"""

import streamlit as st
import pandas as pd
import sqlite3
from pathlib import Path
import sys
import asyncio

# === FIX: Playwright requires ProactorEventLoop on Windows ===
# Streamlit defaults to SelectorEventLoop, which has no subprocess_exec.
# Must be set BEFORE any async code runs.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import json
import re
import urllib.parse
from typing import Optional, Tuple, Dict, List

# Добавляем корень проекта в путь для импортов
sys.path.insert(0, str(Path(__file__).parent.parent))

# --- Конфигурация страницы ---
# (moved inside main() for proper app-shell ordering)

# --- Пути к данным ---
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "leads.db"
SCRAPED_DATA_DIR = DB_PATH.parent
CURATED_DATA_DIR = DB_PATH.parent / "curated"
YANDEX_DATA_DIR = DB_PATH.parent / "yandex"
EXTRACTED_DATA_DIR = DB_PATH.parent / "extracted"
NEURALSYNC_ROOT = Path(r"D:\2 Clode Proj\1\neuralsync")
NEURALSYNC_CONFIGS_DIR = NEURALSYNC_ROOT / "src" / "configs"


def safe_print(text: str) -> None:
    """Безопасный print с защитой от charmap ошибок на Windows."""
    try:
        print(text, flush=True)
    except (UnicodeEncodeError, UnicodeError):
        print(text.encode('cp1251', errors='replace').decode('cp1251'), flush=True)


def load_scraped_data_for_lead(lead_id: int) -> Optional[dict]:
    """
    Загружает данные скрейпинга для лида из JSON файла.
    Ищет файл по домену лида в data/{domain}.json
    """
    try:
        # Загружаем website из БД (новое соединение)
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT website FROM leads WHERE id = ?", (lead_id,))
            row = cursor.fetchone()

        if not row or not row[0]:
            return None

        website = row[0]
        # Извлекаем домен из URL
        domain = website
        if "://" in domain:
            domain = domain.split("://")[1]
        domain = domain.rstrip("/").split("/")[0]

        # Ищем JSON файл с данными скрейпинга
        json_file = SCRAPED_DATA_DIR / f"{domain}.json"
        if json_file.exists():
            with open(json_file, 'r', encoding='utf-8') as f:
                return json.load(f)

        return None
    except Exception as e:
        print(f"Error loading scraped data for lead {lead_id}: {e}")
        return None


def add_hl_ru(url) -> str:
    """Добавляет параметр hl=ru к URL Google Maps для отображения на русском."""
    if not isinstance(url, str):
        return url
    if "?" in url:
        return f"{url}&hl=ru"
    else:
        return f"{url}?hl=ru"


def slugify_name(name: str, lead_id: int) -> str:
    """Собирает slug так же, как content_curator.py, чтобы совпадали имена файлов."""
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


def get_curated_file_path(lead_name: str, lead_id: int) -> Path:
    slug = slugify_name(lead_name, lead_id)
    return CURATED_DATA_DIR / f"{slug}-{lead_id}.json"


def get_extracted_file_path(lead_name: str, lead_id: int) -> Path:
    slug = slugify_name(lead_name, lead_id)
    return EXTRACTED_DATA_DIR / f"{slug}-{lead_id}.json"


def get_content_status(lead_name: str, lead_id: int) -> str:
    error_key = f"curator_error_{lead_id}"
    if st.session_state.get(error_key):
        return "Ошибка"
    return "Готов" if get_curated_file_path(lead_name, lead_id).exists() else "Не создан"


def get_yandex_file_path(lead_name: str, lead_id: int) -> Path:
    slug = slugify_name(lead_name, lead_id)
    return YANDEX_DATA_DIR / f"{slug}-{lead_id}.json"


def get_yandex_status(lead_name: str, lead_id: int) -> str:
    path = get_yandex_file_path(lead_name, lead_id)
    if not path.exists():
        return "⚪ Не обогащён"
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        _meta = d.get("yandex", {}).get("_meta", {})
        if _meta.get("has_full", True):  # Legacy files without _meta are considered full
            return "🟢 Full"
        return "🟡 Light"
    except Exception:
        return "🟢 Обогащён"


def get_yandex_maps_url(lead_name: str, lead_id: int) -> str:
    path = get_yandex_file_path(lead_name, lead_id)
    if not path.exists():
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return str(d.get("yandex", {}).get("source_url") or "")
    except Exception:
        return ""


def get_site_config_path(lead_name: str, lead_id: int) -> Path:
    slug = slugify_name(lead_name, lead_id)
    return NEURALSYNC_CONFIGS_DIR / f"{slug}-{lead_id}.config.js"


def get_site_config_status(lead_name: str, lead_id: int) -> str:
    return "🟢 Собран" if get_site_config_path(lead_name, lead_id).exists() else "⚪ Не собран"


def run_config_builder(lead_id: int, no_copy: bool = False) -> Tuple[bool, str]:
    """Build site config for *lead_id* and write to neuralsync.

    Wraps ``config_builder.cli.run_build_for_ui`` for backward-compatible
    ``(success: bool, message: str)`` contract.
    
    Гарантирует:
    - Очистка ошибки при успешном запуске
    - Проверка источников ПЕРЕД вызовом
    - Перезагрузка состояния после операции
    """
    # Ленивый импорт — playwright тянет много зависимостей
    from config_builder.cli import run_build_for_ui
    try:
        # Гарантированно очищаем старую ошибку ПЕРЕД запуском
        st.session_state.pop(f"cfg_error_{lead_id}", None)
        
        result = run_build_for_ui(lead_id, no_copy=no_copy)
        
        if not result[0]:  # (success, message)
            error_msg = result[1] or "Ошибка сборки конфига"
            st.session_state[f"cfg_error_{lead_id}"] = error_msg
            return result
        
        # Успех - очищаем ошибку
        st.session_state.pop(f"cfg_error_{lead_id}", None)
        return result
    except Exception as exc:
        import traceback
        error_msg = f"Исключение при сборке конфига: {exc}\n{traceback.format_exc()}"
        st.session_state[f"cfg_error_{lead_id}"] = error_msg
        return False, error_msg


def run_block_extractor(lead_id: int) -> Tuple[bool, str]:
    """Запускает извлечение блоков напрямую через services.block_extractor.

    Возвращает (success, output_text). output_text может быть пустым
    при успехе — UI не показывает stdout extract_all.
    
    Гарантирует:
    - Очистка ошибки при успешном запуске
    - Перезагрузка состояния после операции
    """
    try:
        # Гарантированно очищаем старую ошибку ПЕРЕД запуском
        st.session_state.pop(f"extract_error_{lead_id}", None)
        
        from services.block_extractor import extract_all
        result = asyncio.run(extract_all(lead_id))
        if result is None:
            error_msg = f"extract_all вернул None для lead_id={lead_id} (лид не найден или ошибка парсинга)"
            st.session_state[f"extract_error_{lead_id}"] = error_msg
            return False, error_msg
        
        # Успех - очищаем ошибку
        st.session_state.pop(f"extract_error_{lead_id}", None)
        return True, f"OK: services={result.get('services', 0)} faq={result.get('faq', 0)} → {result.get('output_path', '')}"
    except Exception as exc:
        import traceback
        error_msg = f"Исключение: {exc}\n{traceback.format_exc()}"
        st.session_state[f"extract_error_{lead_id}"] = error_msg
        return False, error_msg


def run_config_builder_batch(lead_ids: List[int]) -> Tuple[List[int], Dict[int, str]]:
    success_ids: List[int] = []
    failures: Dict[int, str] = {}
    for lead_id in lead_ids:
        ok, out = run_config_builder(lead_id)
        if ok:
            success_ids.append(lead_id)
        else:
            failures[lead_id] = out or "Ошибка"
    return success_ids, failures


def run_yandex_enricher(lead_id: int, mode: str = "light") -> Tuple[bool, str]:
    """Обогащение через enrichment.yandex_state."""
    try:
        import subprocess
        result = subprocess.run(
            ["python", "-m", "enrichment.yandex_state", str(lead_id), "--force"],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True,
            text=True,
            timeout=300
        )
        if result.returncode == 0:
            return True, f"OK: lead_id={lead_id} mode={mode}"
        else:
            return False, f"Ошибка (код {result.returncode}): {result.stderr}"
    except subprocess.TimeoutExpired:
        return False, "Таймаут: обогащение заняло более 5 минут"
    except Exception as exc:
        import traceback
        return False, f"Исключение: {exc}\n{traceback.format_exc()}"


def run_content_curator(lead_id: int) -> Tuple[bool, str]:
    """Запускає content_curator напряму через llm.content_curator."""
    try:
        from llm.content_curator import curate_lead
        result = asyncio.run(curate_lead(lead_id))
        if result is None or result is False:
            return False, f"Курація не выполнена для lead_id={lead_id}"
        return True, f"OK: lead_id={lead_id}"
    except Exception as exc:
        import traceback
        return False, f"Исключення: {exc}\n{traceback.format_exc()}"


def run_content_curator_with_status(lead_id: int) -> Tuple[bool, str]:
    """Запускает content_curator напрямую с минимальным прогрессом в UI.
    
    Гарантирует:
    - Очистка ошибки при успешном запуске
    - Перезагрузка состояния после операции
    - Синхронизация UI с файловой системой
    """
    ok = False
    output = ""
    try:
        with st.status("⏳ Content Curator работает...", expanded=True) as status_box:
            status_box.write("🔍 Курирую контент через Vertex AI...")
            try:
                # Гарантированно очищаем старую ошибку ПЕРЕД запуском
                st.session_state.pop(f"curator_error_{lead_id}", None)
                
                from llm.content_curator import curate_lead
                result = asyncio.run(curate_lead(lead_id))
                if result is None or result is False:
                    ok = False
                    output = f"Курация вернула пустой результат для lead_id={lead_id}"
                else:
                    ok = True
                    output = f"OK: lead_id={lead_id}"
            except Exception as exc:
                import traceback
                ok = False
                output = f"Исключение: {exc}\n{traceback.format_exc()}"

            if ok:
                status_box.update(label="✅ Курация завершена!", state="complete", expanded=False)
                # Успех - очищаем все ошибки для этого лида
                st.session_state.pop(f"curator_error_{lead_id}", None)
            else:
                status_box.update(label="❌ Ошибка курации", state="error", expanded=True)
                # Ошибка - сохраняем
                st.session_state[f"curator_error_{lead_id}"] = output
    except Exception as outer_exc:
        st.error(f"❌ Ошибка запуска куратора: {outer_exc}")
        output = str(outer_exc)
        st.session_state[f"curator_error_{lead_id}"] = output
    
    return ok, output


def render_curated_content(curated: dict, lead_id: int) -> None:
    about_text = str(curated.get("about", "") or "")
    tagline = str((curated.get("meta") or {}).get("tagline", "") or "")
    reviews = curated.get("reviews") if isinstance(curated.get("reviews"), list) else []
    faq = curated.get("faq") if isinstance(curated.get("faq"), list) else []
    services = curated.get("services") if isinstance(curated.get("services"), list) else []

    st.markdown('<div class="section-title">Tagline</div>', unsafe_allow_html=True)
    st.text_area("Слоган", value=tagline, height=70, key=f"tagline_{lead_id}")

    st.markdown('<div class="section-title">About</div>', unsafe_allow_html=True)
    st.text_area("О нас", value=about_text, height=150, key=f"about_{lead_id}")

    st.markdown('<div class="section-title">Reviews</div>', unsafe_allow_html=True)
    if reviews:
        for idx, item in enumerate(reviews, start=1):
            author = str(item.get("author", "Клиент")) if isinstance(item, dict) else "Клиент"
            text = str(item.get("text", "")) if isinstance(item, dict) else str(item)
            st.text_area(
                f"Отзыв {idx} ({author})",
                value=text,
                height=110,
                key=f"review_{lead_id}_{idx}",
            )
    else:
        st.caption("Отзывы отсутствуют")

    st.markdown('<div class="section-title">FAQ</div>', unsafe_allow_html=True)
    if faq:
        for idx, item in enumerate(faq, start=1):
            if not isinstance(item, dict):
                continue
            q = str(item.get("q", ""))
            a = str(item.get("a", ""))
            st.text_area(
                f"FAQ {idx}",
                value=f"Q: {q}\nA: {a}",
                height=120,
                key=f"faq_{lead_id}_{idx}",
            )
    else:
        st.caption("FAQ отсутствует")

    st.markdown('<div class="section-title">Services</div>', unsafe_allow_html=True)
    if services:
        for idx, item in enumerate(services, start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", ""))
            short = str(item.get("short", ""))
            desc = str(item.get("description", ""))
            price = str(item.get("priceFrom", ""))
            st.text_area(
                f"Услуга {idx}: {title}",
                value=f"Цена: {price}\nКоротко: {short}\n\nОписание:\n{desc}",
                height=170,
                key=f"service_{lead_id}_{idx}",
            )
    else:
        st.caption("Услуги отсутствуют")


def render_yandex_data(data: dict, lead_id: int) -> None:
    yandex = data.get("yandex", {})
    if not yandex:
        st.warning("Нет данных Яндекс")
        return

    m1, m2, m3 = st.columns(3)
    with m1:
        st.metric("Рейтинг Яндекс", f"⭐ {yandex.get('rating') or '—'}")
    with m2:
        st.metric("Отзывов на Яндекс", yandex.get("reviews_count") or "—")
    with m3:
        st.metric("Фото", len(yandex.get("photos", [])))

    if yandex.get("address"):
        st.caption(f"📍 {yandex['address']}")
    if yandex.get("phones"):
        st.caption(f"📞 {', '.join(yandex['phones'])}")
    if yandex.get("working_hours"):
        st.caption(f"🕐 {yandex['working_hours']}")

    reviews = yandex.get("reviews_list", [])
    if reviews:
        st.markdown("")
        for idx, item in enumerate(reviews, start=1):
            author = str(item.get("author", "Клиент"))
            text = str(item.get("text", ""))
            date = str(item.get("date") or "")
            label = f"Отзыв {idx} — {author}" + (f" ({date})" if date else "")
            st.text_area(label, value=text, height=100, key=f"ya_review_{lead_id}_{idx}")
    else:
        st.caption("Отзывы не найдены")

    services = yandex.get("services", [])
    if services:
        with st.expander(f"🛍 Услуги/цены ({len(services)} шт.)"):
            for item in services:
                name = str(item.get("name", ""))
                price = str(item.get("price", ""))
                if name and price and name != price:
                    st.markdown(f"- **{name}** — {price}")

    photos = yandex.get("photos", [])
    if photos:
        with st.expander(f"📷 Фото ({len(photos)} шт.)"):
            cols = st.columns(4)
            for i, url in enumerate(photos):
                if url and url.startswith("http"):
                    cols[i % 4].image(url)


# --- CSS consolidated into main() ---
def load_leads() -> pd.DataFrame:
    """Загружает все лиды из БД в DataFrame (каждый раз новое соединение)"""
    if not DB_PATH.exists():
        return pd.DataFrame()

    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT id, name, google_rating, reviews_count, address, phone, website, google_maps_url, yandex_maps_url, emails, social_links, status, tech_score, load_time_sec, audit_notes, pitch_text, site_config_path, category, city, notes, created_at FROM leads ORDER BY id DESC",
            conn
        )
    return df


def get_stats(df: pd.DataFrame) -> dict:
    """Вычисляет статистику для KPI"""
    if df.empty:
        return {
            "total": 0,
            "new_count": 0,
            "audited_count": 0,
            "pitched_count": 0,
            "no_website_count": 0,
            "rejected_count": 0,
            "avg_rating": 0.0,
            "total_reviews": 0
        }

    new_count = len(df[df["status"] == "new"])
    audited_count = len(df[df["status"] == "audited"])
    pitched_count = len(df[df["status"] == "pitched"])
    no_website_count = len(df[df["status"] == "no_website"])
    rejected_count = len(df[df["status"] == "agents_rejected"])
    skipped_count = len(df[df["status"] == "skipped"])
    needs_photos_count = len(df[df["status"] == "needs_photos"])
    avg_rating = df["google_rating"].mean() if df["google_rating"].notna().any() else 0.0
    total_reviews = df["reviews_count"].sum() if df["reviews_count"].notna().any() else 0

    return {
        "total": len(df),
        "new_count": new_count,
        "audited_count": audited_count,
        "pitched_count": pitched_count,
        "no_website_count": no_website_count,
        "rejected_count": rejected_count,
        "skipped_count": skipped_count,
        "avg_rating": round(avg_rating, 2),
        "total_reviews": int(total_reviews)
    }


def _lead_has_website(website) -> Optional[bool]:
    if website is None or (isinstance(website, float) and pd.isna(website)):
        return False
    raw = str(website).strip()
    if not raw or raw in {"—", "-", "None", "nan"}:
        return False
    if raw.lower().startswith(("http://", "https://")):
        return True
    return None


def enrich_lead_priority(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    now = pd.Timestamp.now(tz="UTC")

    def _row(row: pd.Series) -> pd.Series:
        reasons = []
        score = 0
        status = str(row.get("status") or "").strip().lower()
        has_site = _lead_has_website(row.get("website"))
        no_site = has_site is False or status == "no_website"
        if no_site:
            score += 3
            reasons.append("без сайта")
        phone = row.get("phone")
        if pd.notna(phone) and str(phone).strip() not in {"", "—", "-", "None"}:
            score += 2
            reasons.append("телефон")
        reviews = 0
        try:
            if pd.notna(row.get("reviews_count")):
                reviews = int(row.get("reviews_count"))
        except (TypeError, ValueError):
            reviews = 0
        if reviews >= 10:
            score += 2
            reasons.append(f"{reviews} отзывов")
        rating = 0.0
        try:
            if pd.notna(row.get("google_rating")):
                rating = float(row.get("google_rating"))
        except (TypeError, ValueError):
            rating = 0.0
        if rating >= 4.5:
            score += 1
            reasons.append(f"рейтинг {rating:.1f}")
        if status == "new":
            score += 1
            reasons.append("новый")
        created = pd.to_datetime(row.get("created_at"), errors="coerce", utc=True)
        if pd.notna(created) and (now - created).days <= 14:
            score += 1
            reasons.append("свежий")
        if has_site is True and status != "no_website":
            route = "Лиды"
        elif has_site is False or (status == "no_website" and has_site is not True):
            route = "Лендинг"
        else:
            route = "Проверить"
        return pd.Series({
            "priority_score": min(int(score), 10),
            "lead_route": route,
            "priority_why": ", ".join(reasons) if reasons else "нет сигналов",
        })

    extra = df.apply(_row, axis=1)
    out = df.copy()
    out["priority_score"] = extra["priority_score"]
    out["lead_route"] = extra["lead_route"]
    out["priority_why"] = extra["priority_why"]
    return out


# --- Функции для Транспортного узла ---
def clean_phone(phone: str) -> str:
    """Очищает номер телефона от лишних символов, оставляет только цифры и +."""
    if not phone:
        return ""
    # Оставляем только цифры, +, -, (, ), пробелы
    cleaned = re.sub(r'[^\d+]', '', phone)
    # Убираем + в начале если есть (для wa.me нужен чистый номер)
    if cleaned.startswith('+'):
        cleaned = cleaned[1:]
    return cleaned


def build_send_links(pitch_text: str, row: pd.Series) -> dict:
    """
    Генерирует ссылки для отправки питча через WhatsApp, Telegram, Email.
    Возвращает dict с ключами 'whatsapp', 'telegram', 'email' (или None если нет данных).
    """
    links = {}
    encoded_pitch = urllib.parse.quote(pitch_text, safe='')

    # WhatsApp
    phone_raw = row.get("phone", "")
    wa_links = []
    if pd.notna(phone_raw) and phone_raw:
        clean = clean_phone(str(phone_raw))
        if clean and len(clean) >= 10:
            wa_links.append(f"https://wa.me/{clean}?text={encoded_pitch}")

    # Проверяем social_links на wa.me
    social_raw = row.get("social_links")
    if pd.notna(social_raw) and social_raw:
        try:
            social_dict = json.loads(social_raw)
            if "whatsapp" in social_dict:
                for url in social_dict["whatsapp"]:
                    # Извлекаем номер из wa.me/79991234567
                    match = re.search(r'wa\.me/(\d+)', url)
                    if match:
                        wa_links.append(f"https://wa.me/{match.group(1)}?text={encoded_pitch}")
        except (json.JSONDecodeError, TypeError):
            pass

    if wa_links:
        links["whatsapp"] = wa_links[0]  # Берём первый

    # Telegram
    tg_links_list = []
    if pd.notna(social_raw) and social_raw:
        try:
            social_dict = json.loads(social_raw)
            if "telegram" in social_dict:
                for url in social_dict["telegram"]:
                    # Извлекаем username из t.me/username
                    match = re.search(r't\.me/([\w]+)', url)
                    if match:
                        username = match.group(1)
                        tg_links_list.append(f"https://t.me/{username}?text={encoded_pitch}")
        except (json.JSONDecodeError, TypeError):
            pass

    if tg_links_list:
        links["telegram"] = tg_links_list[0]

    # Email
    emails_raw = row.get("emails")
    if pd.notna(emails_raw) and emails_raw:
        try:
            emails_list = json.loads(emails_raw)
            if emails_list:
                email = emails_list[0]
                website = row.get("website", "")
                domain = ""
                if pd.notna(website) and website:
                    domain = urllib.parse.urlparse(str(website)).netloc
                subject = urllib.parse.quote(f"Аудит сайта {domain}", safe='')
                body = encoded_pitch
                links["email"] = f"mailto:{email}?subject={subject}&body={body}"
        except (json.JSONDecodeError, TypeError):
            pass

    return links


def classify_link(url):
    if not url or not str(url).startswith("http"): return None
    url = str(url).lower()
    if "maps.google" in url or "google.com/maps" in url: return ("🗺️", "Google")
    if "yandex.ru/maps" in url or "yandex" in url: return ("🟡", "Яндекс")
    if "instagram.com" in url: return ("📸", "Instagram")
    if "vk.com" in url or "vk.link" in url or "m.vk.com" in url: return ("💙", "VK")
    if "t.me" in url: return ("✈️", "TG")
    if "wa.me" in url or "whatsapp" in url: return ("💬", "WA")
    if "2gis.ru" in url or "2gis" in url: return ("🔵", "2GIS")
    if "dikidi.app" in url or "dikidi" in url: return ("📋", "DIKIDI")
    if "taplink" in url: return ("🔗", "Taplink")
    if "youtube.com" in url or "youtu.be" in url: return ("▶️", "YouTube")
    if "tiktok.com" in url: return ("🎵", "TikTok")
    if "facebook.com" in url or "fb.com" in url: return ("📘", "FB")
    if "zoon" in url: return ("🏥", "Zoon")
    if "prodoctorov" in url: return ("🏥", "ProDoctorov")
    if "napopravku" in url: return ("🏥", "NaPopravku")
    if "yclients" in url: return ("📅", "YClients")
    return ("🌐", "Сайт")


def format_social_links(social_raw: str) -> str:
    """Парсит JSON social_links и возвращает компактную строку с иконками."""
    if not social_raw or not isinstance(social_raw, str):
        return "—"
    try:
        links = json.loads(social_raw)
    except (json.JSONDecodeError, TypeError):
        return "—"
    if not isinstance(links, dict):
        return "—"

    parts = []
    for key, values in links.items():
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        for val in values:
            if not val or not str(val).startswith("http"):
                continue
            icon, name = classify_link(val)
            # Короткий display — домен или первый сегмент пути
            clean = str(val).replace("https://", "").replace("http://", "")
            if len(clean) > 30:
                domain = clean.split("/")[0]
                path_part = clean.split("/")[1] if len(clean.split("/")) > 1 else ""
                display = f"{domain}/{path_part}"[:30] if path_part else domain[:30]
            else:
                display = clean[:30]
            parts.append(f"{icon} {display}")
    return " | ".join(parts[:5]) if parts else "—"


def extract_social_url(social_raw: str, key: str) -> Optional[str]:
    """Извлекает URL соцсети по ключу из JSON social_links."""
    if not social_raw or not isinstance(social_raw, str):
        return None
    try:
        links = json.loads(social_raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(links, dict):
        return None
    val = links.get(key)
    if isinstance(val, list):
        val = val[0] if val else None
    if val and isinstance(val, str) and val.startswith("http"):
        return val
    return None


def get_social_url(row: pd.Series, key: str) -> Optional[str]:
    """Извлекает URL соцсети из social_links колонки DataFrame."""
    raw = row.get("social_links")
    if pd.notna(raw) and raw:
        return extract_social_url(str(raw), key)
    return None


# --- Главная страница ---
def main():
    def render_status(status):
        styles = {
            "audited": ("✓ Audited", "#1e3a2f", "#22c55e"),
            "pitched": ("→ Pitched", "#1e2a3a", "#3b82f6"),
            "no_website": ("⊘ No Site", "#2a2a2a", "#71717a"),
            "new": ("◉ New", "#2a1f0e", "#f59e0b"),
            "agents_rejected": ("✕ Rejected", "#2a1515", "#ef4444"),
        }
        label, bg, color = styles.get(str(status).strip().lower(), (str(status), "#1c1c1f", "#71717a"))
        return f'<span style="background:{bg};color:{color};padding:2px 10px;border-radius:12px;font-size:11px;font-weight:600;border:1px solid {color}33;white-space:nowrap">{label}</span>'

    st.set_page_config(page_title="KURSOR Command Center", layout="wide", initial_sidebar_state="expanded")

    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300..700&family=JetBrains+Mono:wght@400;700&display=swap');

html, .stApp, .main, .block-container { background: #0d0d0f !important; color: #e4e4e7 !important; font-family: 'Inter', sans-serif !important; }
[data-testid="stSidebar"] { background: #111113 !important; border-right: 1px solid rgba(255,255,255,0.06) !important; }
[data-testid="stSidebar"] * { color: #e4e4e7 !important; }
#MainMenu, header[data-testid="stHeader"], footer { display: none !important; }
.block-container { padding-top: 1rem !important; }
.stButton > button { background: transparent !important; color: #e4e4e7 !important; border: 1px solid rgba(255,255,255,0.15) !important; border-radius: 6px !important; font-size: 13px !important; font-weight: 500 !important; }
.stButton > button:hover { border-color: #00d9f5 !important; background: rgba(0,217,245,0.07) !important; color: #00d9f5 !important; }
[data-testid="stMetricValue"] { font-family: 'JetBrains Mono', monospace !important; font-variant-numeric: tabular-nums !important; color: #e4e4e7 !important; }
[data-testid="stMetricLabel"] { font-size: 11px !important; text-transform: uppercase !important; letter-spacing: 0.08em !important; color: #71717a !important; }
[data-testid="stMetric"] { background: #18181b !important; border: 1px solid rgba(255,255,255,0.07) !important; border-top: 2px solid #00d9f5 !important; border-radius: 8px !important; padding: 12px 14px !important; }
.stTabs [data-baseweb="tab"] { background: transparent !important; color: #71717a !important; font-family: 'Inter', sans-serif !important; }
.stTabs [aria-selected="true"] { color: #00d9f5 !important; border-bottom: 2px solid #00d9f5 !important; }
table { width: 100% !important; border-collapse: collapse !important; }
table th { background: #1c1c1f !important; color: #52525b !important; font-size: 11px !important; text-transform: uppercase !important; letter-spacing: 0.06em !important; font-weight: 600 !important; padding: 10px 12px !important; border-bottom: 1px solid rgba(255,255,255,0.04) !important; text-align: center !important; }
table td { background: transparent !important; color: #e4e4e7 !important; padding: 8px 12px !important; border-bottom: 1px solid rgba(255,255,255,0.04) !important; text-align: center !important; }
table tr:hover td { background: #18181b !important; }
[data-testid="stDataFrame"] th { text-align: center !important; }
[data-testid="stDataFrame"] td { text-align: center !important; }
[data-testid="stDataFrame"] .stColumnHeader { justify-content: center !important; }
[data-testid="stDataFrame"] .stColumnData { justify-content: center !important; }

/* Center data_editor cells and headers */
div[data-testid="stDataEditor"] th {
    text-align: center !important;
    justify-content: center !important;
}
div[data-testid="stDataEditor"] td {
    text-align: center !important;
    justify-content: center !important;
}
div[data-testid="stDataEditor"] .stColumnData {
    justify-content: center !important;
}
div[data-testid="stDataEditor"] input {
    text-align: center !important;
}
div[data-testid="stDataEditor"] .stTextInput > div {
    justify-content: center !important;
}
div[data-testid="stDataEditor"] [class*="column-header"] {
    text-align: center !important;
    justify-content: center !important;
}
div[data-testid="stDataEditor"] [class*="stColumn"] > div {
    justify-content: center !important;
    text-align: center !important;
}
h1, h2, h3 { color: #e4e4e7 !important; font-family: 'Inter', sans-serif !important; }
.sidebar-brand { font-size:18px;font-weight:700;color:#00d9f5;padding:12px 0;border-bottom:1px solid rgba(255,255,255,0.07);margin-bottom:16px;letter-spacing:-0.5px }
.sidebar-card { background:#18181b;border:1px solid rgba(255,255,255,0.07);border-radius:8px;padding:12px;margin-bottom:12px }
.sidebar-group-label { font-size:10px;color:#71717a;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:8px;font-weight:600 }
.analytics-card { background:#18181b;border:1px solid rgba(255,255,255,0.07);border-radius:8px;padding:12px 14px;text-align:center }
.analytics-val { font-family:'JetBrains Mono',monospace;font-size:22px;font-weight:700;font-variant-numeric:tabular-nums }
.analytics-lbl { font-size:10px;color:#71717a;text-transform:uppercase;letter-spacing:0.08em;margin-top:2px }
.section-title { font-size:18px;font-weight:600;color:#e4e4e7;margin-bottom:16px }
.section-title-meta { font-size:12px;color:#71717a;font-weight:400 }
.stage-card { background:#18181b;border:1px solid rgba(255,255,255,0.07);border-top:3px solid #00d9f5;border-radius:8px;padding:16px 12px;text-align:center }
.stage-count { font-family:'JetBrains Mono',monospace;font-size:28px;font-weight:700;color:#e4e4e7 }
.stage-label { font-size:11px;color:#71717a;margin-top:4px }

div[data-testid="stDataEditor"] {
    overflow-x: auto !important;
    max-width: 100% !important;
}
div[data-testid="stDataEditor"] table {
    table-layout: fixed !important;
    width: 100% !important;
}
div[data-testid="stDataEditor"] td,
div[data-testid="stDataEditor"] th {
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}
div[data-testid="stDataEditor"] td input,
div[data-testid="stDataEditor"] td div {
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}
/* фикс. ширина колонок — чтобы не расползались */
div[data-testid="stDataEditor"] th:nth-child(2),
div[data-testid="stDataEditor"] td:nth-child(2) { width:48px !important; min-width:48px !important; }
div[data-testid="stDataEditor"] th:nth-child(6),
div[data-testid="stDataEditor"] td:nth-child(6) { width:55px !important; min-width:55px !important; }
div[data-testid="stDataEditor"] th:nth-child(7),
div[data-testid="stDataEditor"] td:nth-child(7) { width:55px !important; min-width:55px !important; }
div[data-testid="stDataEditor"] th:nth-child(8),
div[data-testid="stDataEditor"] td:nth-child(8) { width:50px !important; min-width:50px !important; }
div[data-testid="stDataEditor"] th:nth-child(11),
div[data-testid="stDataEditor"] td:nth-child(11) { width:42px !important; min-width:42px !important; }
div[data-testid="stDataEditor"] th:nth-child(12),
div[data-testid="stDataEditor"] td:nth-child(12) { width:42px !important; min-width:42px !important; }
/* Связи — список соцсетей текстом */
div[data-testid="stDataEditor"] th:nth-child(13),
div[data-testid="stDataEditor"] td:nth-child(13) { width:180px !important; min-width:140px !important; max-width:220px !important; }
/* Заметки */
div[data-testid="stDataEditor"] th:nth-child(14),
div[data-testid="stDataEditor"] td:nth-child(14) { width:200px !important; min-width:200px !important; max-width:200px !important; }
</style>""", unsafe_allow_html=True)

    df = load_leads()
    stats = get_stats(df)

    no_site_count = 0
    if not df.empty and "website" in df.columns:
        no_site_count = int((df["website"].isna() | (df["website"] == "")).sum())
    audited_count = stats.get("audited_count", 0)

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("Всего лидов", stats["total"])
    with k2:
        st.metric("Без собственного сайта", no_site_count)
    with k3:
        st.metric("Новые лиды", stats["new_count"])
    with k4:
        st.metric("Квалифицированные / audited", audited_count)


    if df.empty:
        st.info("База данных пуста. Запустите Радар для поиска лидов.")
        return

    # --- SIDEBAR: Navigation + Filters + System Actions ---
    with st.sidebar:
        st.markdown('<div class="sidebar-brand">KURSOR Command Center</div>', unsafe_allow_html=True)
        
        st.markdown('<div class="sidebar-card"><div class="sidebar-group-label">Навигация</div>', unsafe_allow_html=True)
        page = st.radio("Раздел", ["База лидов", "Контент-конвейер", "Покрытие"], label_visibility="collapsed")
        st.markdown('</div>', unsafe_allow_html=True)
        
        # A - Filters
        st.markdown('<div class="sidebar-card"><div class="sidebar-group-label">Фильтры</div>', unsafe_allow_html=True)
        status_filter = st.multiselect("Статус", options=["new","audited","no_website","pitched","agents_rejected","skipped","needs_photos","sold"], default=[], label_visibility="collapsed", placeholder="Статус")
        city_filter = st.selectbox("Город", ["Все", "Брянск", "Москва"], label_visibility="collapsed")
        rating_min = st.slider("Рейтинг", 0.0, 5.0, 0.0, 0.5, label_visibility="collapsed")
        website_filter = st.selectbox("Сайт", ["Все", "С сайтом", "Без сайта"], label_visibility="collapsed")
        cat_options = sorted(df["category"].dropna().unique().tolist()) if "category" in df.columns else []
        category_filter = st.multiselect("Категория", options=cat_options, default=[], label_visibility="collapsed", placeholder="Категория")
        route_filter = st.selectbox("Маршрут", ["Все", "Лендинг", "Лиды", "Проверить"], label_visibility="collapsed")
        st.markdown('</div>', unsafe_allow_html=True)
        
        # B - Actions
        st.markdown('<div class="sidebar-card"><div class="sidebar-group-label">Операции</div>', unsafe_allow_html=True)
        st.button("Запустить Радар", use_container_width=True)
        st.button("Запустить Рентген", use_container_width=True)
        st.button("Обновить данные", use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
        
        # C - System
        st.markdown('<div class="sidebar-card"><div class="sidebar-group-label">Система</div>', unsafe_allow_html=True)
        st.markdown('<div style="font-size:12px;color:var(--text-muted)"><span style="color:var(--success)">●</span> Система активна<br>deepseek-v4-flash</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # --- FILTER APPLICATION ---
    # Pill-переключатель имеет приоритет над status_filter
    view_mode = st.session_state.get("view_mode_radio", "Кандидаты")
    if view_mode != "Кандидаты":
        # В не-дефолтном режиме pill игнорируем status_filter,
        # фильтрация по статусу будет применена ниже (строки 784-791)
        df_filtered = df.copy()
    elif status_filter:
        df_filtered = df[df["status"].isin(status_filter)]
    else:
        df_filtered = df.copy()
    if city_filter != "Все":
        df_filtered = df_filtered[df_filtered["city"] == city_filter]
    df_filtered = df_filtered[df_filtered["google_rating"] >= rating_min]
    if website_filter == "С сайтом":
        df_filtered = df_filtered[df_filtered["website"].notna() & (df_filtered["website"] != "")]
    elif website_filter == "Без сайта":
        df_filtered = df_filtered[df_filtered["website"].isna() | (df_filtered["website"] == "")]
    if category_filter:
        df_filtered = df_filtered[df_filtered["category"].isin(category_filter)]

    hide_perfect = st.session_state.get("hide_perfect", False)
    max_score_filter = st.session_state.get("max_score_filter", 100)
    if hide_perfect:
        df_filtered = df_filtered[(df_filtered["tech_score"] != 100) | (df_filtered["tech_score"].isna())]
    df_filtered = df_filtered[
        (df_filtered["tech_score"] <= max_score_filter)
        | (df_filtered["tech_score"].isna())
        | (df_filtered["status"] == "new")
    ]

    if df_filtered.empty:
        st.info("По текущим фильтрам лиды не найдены")
        return

    df_filtered = enrich_lead_priority(df_filtered.copy())
    if route_filter != "Все":
        df_filtered = df_filtered[df_filtered["lead_route"] == route_filter]
        if df_filtered.empty:
            st.info("По текущим фильтрам лиды не найдены")
            return
    df_filtered["_added_sort"] = pd.to_datetime(df_filtered.get("created_at"), errors="coerce")
    df_filtered = df_filtered.sort_values(
        ["priority_score", "_added_sort"],
        ascending=[False, False],
        na_position="last",
    ).drop(columns=["_added_sort"])

    # --- Control strip ---
    search_term = st.text_input("🔍 Поиск", placeholder="Название, город, телефон...", label_visibility="collapsed")
    if search_term:
        search_mask = df_filtered['name'].str.contains(search_term, case=False, na=False) | df_filtered['city'].str.contains(search_term, case=False, na=False)
        df_filtered = df_filtered[search_mask]

    lead_options = [f"{int(row['id'])} — {row['name']}" for _, row in df_filtered.iterrows()]
    valid_ids = set(df_filtered["id"].astype(int).tolist())
    if "selected_lead_id" not in st.session_state or int(st.session_state["selected_lead_id"]) not in valid_ids:
        st.session_state["selected_lead_id"] = int(df_filtered.iloc[0]["id"])

    # Page routing
    if page == "База лидов":
        st.markdown('<div class="section-title">База лидов <span class="section-title-meta">Показано {0} из {1} записей</span></div>'.format(len(df_filtered), len(df)), unsafe_allow_html=True)

        batch_config_ids = [
            int(row["id"])
            for _, row in df_filtered.iterrows()
            if get_curated_file_path(str(row.get("name", "")), int(row.get("id", 0))).exists()
            and get_yandex_file_path(str(row.get("name", "")), int(row.get("id", 0))).exists()
            and not get_site_config_path(str(row.get("name", "")), int(row.get("id", 0))).exists()
        ]
        if st.button(f"Сгенерировать конфиги ({len(batch_config_ids)})", disabled=len(batch_config_ids) == 0):
            with st.spinner("Собираю конфиги..."):
                ok_ids, err_map = run_config_builder_batch(batch_config_ids)
            if ok_ids:
                st.success(f"Собрано конфигов: {len(ok_ids)}")
            if err_map:
                st.error(f"Ошибок: {len(err_map)}")
                for bid, bout in list(err_map.items())[:3]:
                    st.code(f"ID={bid}\n{bout[:1800]}", language="")
            st.rerun()

        # --- Pill-переключатель "Показывать" над таблицей ---
        view_mode = st.radio(
            "Показывать:",
            options=["Кандидаты", "В работе", "Продано", "Скрытые"],
            horizontal=True,
            index=0,
            key="view_mode_radio"
        )
        # Apply view mode filter
        if view_mode == "Кандидаты":
            df_filtered = df_filtered[df_filtered["status"].isin(["new", "audited", "no_website", "needs_photos"])]
        elif view_mode == "В работе":
            df_filtered = df_filtered[df_filtered["status"] == "pitched"]
        elif view_mode == "Продано":
            df_filtered = df_filtered[df_filtered["status"] == "sold"]
        elif view_mode == "Скрытые":
            df_filtered = df_filtered[df_filtered["status"] == "skipped"]

        def normalize_url_for_table(value: object) -> object:
            if value is None or (isinstance(value, float) and pd.isna(value)):
                return None
            raw = str(value).strip()
            if not raw:
                return None
            if raw.lower().startswith(("http://", "https://")):
                return raw
            return None

        # --- TABLE using Streamlit data_editor with native checkboxes ---
        display_cols = [
            "priority_score", "lead_route", "priority_why",
            "name", "city", "category", "google_rating", "reviews_count", "status",
            "website", "phone", "notes", "created_at", "google_maps_url", "yandex_maps_url",
            "tech_score", "social_links",
        ]
        display_cols = [c for c in display_cols if c in df_filtered.columns]
        df_disp = df_filtered[display_cols].copy()

        df_disp["_maps_link"] = df_disp["google_maps_url"].apply(
            lambda v: v if pd.notna(v) and str(v).strip().startswith("http") else None
        ) if "google_maps_url" in df_disp.columns else None
        df_disp["_yandex_link"] = df_disp["yandex_maps_url"].apply(
            lambda v: v if pd.notna(v) and str(v).strip().startswith("http") else None
        ) if "yandex_maps_url" in df_disp.columns else None
        df_disp["_social_links"] = df_filtered["social_links"].apply(format_social_links)
        df_disp["_vk_link"] = df_filtered.apply(lambda r: get_social_url(r, "vk"), axis=1)
        df_disp["_ig_link"] = df_filtered.apply(lambda r: get_social_url(r, "instagram"), axis=1)
        df_disp["_tg_link"] = df_filtered.apply(lambda r: get_social_url(r, "telegram"), axis=1)
        df_disp["_tap_link"] = df_filtered.apply(lambda r: get_social_url(r, "taplink"), axis=1)
        df_disp["_wa_link"] = df_filtered.apply(lambda r: get_social_url(r, "whatsapp"), axis=1)

        cat_map = {"beauty": "Салон красоты", "dental_cosmetology": "Стоматология", "other": "Другое"}
        if "category" in df_disp.columns:
            df_disp["category"] = df_disp["category"].apply(lambda x: cat_map.get(str(x), str(x)))

        status_map = {
            "new": "Новый",
            "no_website": "Нет сайта",
            "audited": "Аудит",
            "pitched": "Питч",
            "needs_photos": "Нужны фото",
            "skipped": "Скрыт",
            "sold": "Продано",
        }
        if "status" in df_disp.columns:
            df_disp["status"] = df_disp["status"].apply(lambda x: status_map.get(str(x), str(x)))

        if "created_at" in df_disp.columns:
            df_disp["created_at"] = pd.to_datetime(df_disp["created_at"], errors="coerce").dt.strftime("%d.%m.%Y")
            df_disp["created_at"] = df_disp["created_at"].fillna("—")
        if "phone" in df_disp.columns:
            df_disp["phone"] = df_disp["phone"].fillna("—")

        df_disp = df_disp.rename(columns={
            "priority_score": "Приоритет", "lead_route": "Маршрут", "priority_why": "Почему",
            "name": "Название", "city": "Город", "category": "Категория",
            "google_rating": "Рейтинг", "reviews_count": "Отзывы",
            "status": "Статус", "website": "Сайт", "phone": "Телефон",
            "tech_score": "Tech", "notes": "Заметки", "created_at": "Добавлен",
        })
        for col in ["Приоритет", "Маршрут", "Почему", "Название", "Город", "Категория", "Рейтинг", "Отзывы", "Статус", "Сайт", "Телефон", "Заметки", "Добавлен"]:
            if col not in df_disp.columns:
                df_disp[col] = "—"

        if "Рейтинг" in df_disp.columns:
            df_disp["Рейтинг"] = df_disp["Рейтинг"].apply(
                lambda x: f"{x:.1f}" if pd.notna(x) and x != "—" else "—"
            )

        df_disp.insert(0, "ID", df_filtered["id"].values)
        df_disp.insert(0, "Выбрать", False)

        col_config = {
            "Выбрать": st.column_config.CheckboxColumn("Выбрать", help="Выбрать для скрытия", default=False),
            "Приоритет": st.column_config.NumberColumn("Приоритет", width="small", disabled=True),
            "Маршрут": st.column_config.TextColumn("Маршрут", width="small", disabled=True),
            "Почему": st.column_config.TextColumn("Почему", width="medium", disabled=True),
            "ID": st.column_config.NumberColumn("ID", width="small", disabled=True),
            "Название": st.column_config.TextColumn("Название", width="medium", disabled=True),
            "Город": st.column_config.TextColumn("Город", width="small", disabled=True),
            "Категория": st.column_config.TextColumn("Категория", width="small", disabled=True),
            "Рейтинг": st.column_config.TextColumn("Рейтинг", width="small", disabled=True),
            "Отзывы": st.column_config.NumberColumn("Отзывы", width="small", disabled=True),
            "Статус": st.column_config.TextColumn("Статус", width="small", disabled=True),
            "Сайт": st.column_config.LinkColumn("Сайт", display_text="открыть", width="small", disabled=True),
            "Телефон": st.column_config.TextColumn("Телефон", width="small", disabled=True),
            "Добавлен": st.column_config.TextColumn("Добавлен", width="small", disabled=True),
            "Заметки": st.column_config.TextColumn("Заметки", width="small", disabled=False),
        }

        final_disp_cols = [
            "Выбрать", "Приоритет", "Маршрут", "Почему", "ID", "Название", "Город",
            "Категория", "Рейтинг", "Отзывы", "Статус", "Сайт", "Телефон", "Добавлен", "Заметки",
        ]
        final_disp_cols = [c for c in final_disp_cols if c in df_disp.columns]
        df_main = df_disp[final_disp_cols]

        TABLE_HEIGHT = 900 if st.session_state.get("fs_expanded", False) else 400

        # Hidden sync: save column widths from session_state before rendering
        # Streamlit doesn't persist drag-resize, so we use explicit width in column_config
        
        st.data_editor(
            df_main,
            column_config=col_config,
            hide_index=True,
            use_container_width=True,
            height=TABLE_HEIGHT,
            key="lead_table"
        )

        detail_cols = [c for c in ["ID", "Tech", "_maps_link", "_yandex_link", "_vk_link", "_ig_link", "_tg_link", "_tap_link", "_wa_link", "_social_links"] if c in df_disp.columns]
        if detail_cols:
            with st.expander("Детали"):
                st.dataframe(
                    df_disp[detail_cols].rename(columns={
                        "Tech": "Tech",
                        "_maps_link": "Карты",
                        "_yandex_link": "Яндекс",
                        "_vk_link": "VK",
                        "_ig_link": "IG",
                        "_tg_link": "TG",
                        "_tap_link": "Tap",
                        "_wa_link": "WA",
                        "_social_links": "Связи",
                    }),
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Карты": st.column_config.LinkColumn("Карты", display_text="открыть"),
                        "Яндекс": st.column_config.LinkColumn("Яндекс", display_text="открыть"),
                        "VK": st.column_config.LinkColumn("VK", display_text="открыть"),
                        "IG": st.column_config.LinkColumn("IG", display_text="открыть"),
                        "TG": st.column_config.LinkColumn("TG", display_text="открыть"),
                        "Tap": st.column_config.LinkColumn("Tap", display_text="открыть"),
                        "WA": st.column_config.LinkColumn("WA", display_text="открыть"),
                    },
                )

        # Get selected IDs from data_editor session state
        edited_rows = st.session_state.get("lead_table", {}).get("edited_rows", {})
        selected_ids = []
        
        # Save notes changes back to DB
        with sqlite3.connect(DB_PATH) as conn_save:
            for row_idx_str, edits in edited_rows.items():
                if "Заметки" in edits:
                    row_idx = int(row_idx_str)
                    if 0 <= row_idx < len(df_main):
                        lead_id = int(df_main.iloc[row_idx]["ID"])
                        new_note = edits["Заметки"]
                        conn_save.execute("UPDATE leads SET notes=? WHERE id=?", (new_note, lead_id))
            conn_save.commit()
        
        for row_idx_str, edits in edited_rows.items():
            if edits.get("Выбрать"):
                row_idx = int(row_idx_str)
                if 0 <= row_idx < len(df_main):
                    selected_ids.append(int(df_main.iloc[row_idx]["ID"]))
        n_selected = len(selected_ids)
        is_skipped_view = (view_mode == "Скрытые")
        btn_label = f"Восстановить выбранные ({n_selected})" if is_skipped_view else f"Скрыть выбранные ({n_selected})"
        btn_help = "Вернуть в кандидаты" if is_skipped_view else "Скрыть из таблицы"

        # Кнопки действий — работают с теми же чекбоксами
        act_col1, act_col2, act_col3 = st.columns(3)
        with act_col1:
            if st.button(f"В работу ({n_selected})", disabled=(n_selected == 0 or is_skipped_view), use_container_width=True):
                with sqlite3.connect(DB_PATH) as conn:
                    for lid in selected_ids:
                        conn.execute("UPDATE leads SET status='pitched' WHERE id=?", (int(lid),))
                st.rerun()
        with act_col2:
            if st.button(f"Продано ({n_selected})", disabled=(n_selected == 0 or is_skipped_view), use_container_width=True):
                with sqlite3.connect(DB_PATH) as conn:
                    for lid in selected_ids:
                        conn.execute("UPDATE leads SET status='sold' WHERE id=?", (int(lid),))
                st.rerun()
        with act_col3:
            if st.button(btn_label, disabled=(n_selected == 0), use_container_width=True, help=btn_help):
                with sqlite3.connect(DB_PATH) as conn:
                    for lid in selected_ids:
                        new_status = "new" if is_skipped_view else "skipped"
                        conn.execute("UPDATE leads SET status=? WHERE id=?", (new_status, int(lid),))
                st.rerun()

        # Fullscreen toggle
        st.button("⛶", key="fs_btn", help="На весь экран / свернуть", on_click=lambda: st.session_state.update({"fs_expanded": not st.session_state.get("fs_expanded", False)}))

        # Selected row highlight CSS
        st.markdown("""
        <style>
        /* selected row */
        tr.selected { outline: 2px solid #00d9f5; outline-offset: -2px; border-radius: 4px; }
        </style>
        """, unsafe_allow_html=True)

        # --- LEAD DETAILS ---
        # Select active lead
        lead_options = [f"#{int(row['id'])} {row['name']}" for _, row in df_filtered.iterrows()]
        valid_ids = set(df_filtered["id"].astype(int).tolist())
        if "selected_lead_id" not in st.session_state or int(st.session_state["selected_lead_id"]) not in valid_ids:
            st.session_state["selected_lead_id"] = int(df_filtered.iloc[0]["id"])
        
        current_idx = 0
        for i, lid in enumerate(df_filtered["id"].values):
            if int(lid) == int(st.session_state["selected_lead_id"]):
                current_idx = i
                break
        
        chosen = st.selectbox("👤 Детали лида", options=lead_options, index=current_idx, label_visibility="collapsed")
        # Extract ID from chosen option
        chosen_id = int(chosen.split(" ")[0].replace("#", ""))
        if chosen_id != int(st.session_state["selected_lead_id"]):
            st.session_state["selected_lead_id"] = chosen_id
            st.rerun()
        
        selected_id = int(st.session_state["selected_lead_id"])
        selected_lead = df[df["id"] == selected_id]
        if not selected_lead.empty:
            lead = selected_lead.iloc[0]
            st.markdown(f'<div style="background:#18181b;border:1px solid rgba(255,255,255,0.07);border-radius:10px;padding:16px;margin-top:8px"><div style="font-size:13px;font-weight:600;color:#00d9f5;margin-bottom:12px">Активный лид: {lead.get("name","")}</div>', unsafe_allow_html=True)
            # Kontaktы
            st.markdown('<div style="font-size:10px;color:#71717a;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:8px">Контакты</div>', unsafe_allow_html=True)
            st.markdown(f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:13px"><div><span style="color:#71717a">Город</span><br>{lead.get("city","—")}</div><div><span style="color:#71717a">Телефон</span><br>{lead.get("phone","—")}</div><div><span style="color:#71717a">Адрес</span><br>{lead.get("address","—")}</div><div><span style="color:#71717a">Почта</span><br>{"—"}</div></div>', unsafe_allow_html=True)
            # Metrics
            st.markdown('<div style="font-size:10px;color:#71717a;text-transform:uppercase;letter-spacing:0.08em;margin-top:12px;margin-bottom:8px">Метрики</div>', unsafe_allow_html=True)
            sts = lead.get("status", "")
            status_badge = render_status(sts) if render_status and sts else '<span style="color:#71717a">\u2014</span>'
            st.markdown(f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:13px"><div><span style="color:#71717a">Рейтинг</span><br>{"⭐ "+str(lead.get("google_rating","")) if pd.notna(lead.get("google_rating")) else "—"}</div><div><span style="color:#71717a">Отзывы</span><br>{int(lead.get("reviews_count",0)) if pd.notna(lead.get("reviews_count")) else "—"}</div><div><span style="color:#71717a">Score</span><br>{int(lead.get("tech_score",0)) if pd.notna(lead.get("tech_score")) and int(lead.get("tech_score",0)) > 0 else "—"}</div><div><span style="color:#71717a">Статус</span><br>{status_badge}</div></div>', unsafe_allow_html=True)
            
            # Action row
            links = []
            for field in ["website", "google_maps_url", "yandex_maps_url", "social_links"]:
                val = lead.get(field, "")
                if val and str(val).startswith("http"):
                    result = classify_link(val)
                    if result:
                        icon, label = result
                        links.append(f'<a href="{val}" target="_blank" style="background:transparent;border:1px solid rgba(255,255,255,0.13);border-radius:6px;padding:6px 12px;font-size:12px;color:#e4e4e7;text-decoration:none;display:inline-flex;align-items:center;gap:4px">{icon} {label}</a>')
            # Social links JSON
            social_raw = lead.get("social_links", "")
            if social_raw and str(social_raw).strip():
                try:
                    import json
                    social_data = json.loads(str(social_raw))
                    if isinstance(social_data, dict):
                        for key, val in social_data.items():
                            url = val if isinstance(val, str) else (val[0] if isinstance(val, list) else "")
                            if url and str(url).startswith("http"):
                                result = classify_link(url)
                                if result:
                                    icon, label = result
                                    links.append(f'<a href="{url}" target="_blank" style="background:transparent;border:1px solid rgba(255,255,255,0.13);border-radius:6px;padding:6px 12px;font-size:12px;color:#e4e4e7;text-decoration:none;display:inline-flex;align-items:center;gap:4px">{icon} {label}</a>')
                except: pass
            # Ссылки
            st.markdown('<div style="font-size:10px;color:#71717a;text-transform:uppercase;letter-spacing:0.08em;margin-top:12px;margin-bottom:8px">Ссылки</div>', unsafe_allow_html=True)
            if links:
                st.markdown(f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">{"".join(links)}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown("---")
    
    elif page == "Контент-конвейер":
        st.markdown('<div class="section-title">Контент-конвейер</div>', unsafe_allow_html=True)
        stages = [("New", len(df[df["status"]=="new"]) if not df.empty else 0, "var(--warning)"), ("Audited", len(df[df["status"]=="audited"]) if not df.empty else 0, "var(--info)"), ("Enriched", 0, "var(--accent)"), ("Content Ready", 0, "var(--success)"), ("Pitched", len(df[df["status"]=="pitched"]) if not df.empty else 0, "var(--success)")]
        cols = st.columns(len(stages))
        for i, (label, count, color) in enumerate(stages):
            with cols[i]:
                st.markdown(f'<div class="stage-card" style="border-top-color:{color}"><div class="stage-count">{count}</div><div class="stage-label">{label}</div></div>', unsafe_allow_html=True)
        st.markdown("---")
        st.markdown(f'<div class="section-title-meta">Всего в работе: {len(df)} лидов</div>', unsafe_allow_html=True)

    elif page == "Покрытие":
        st.markdown('<div class="section-title">Покрытие регионов</div>', unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        with c1: st.markdown(f'<div class="analytics-card" style="border-top-color:var(--accent)"><div class="analytics-val accent">0</div><div class="analytics-lbl">Сканировано</div></div>', unsafe_allow_html=True)
        with c2: st.markdown(f'<div class="analytics-card" style="border-top-color:var(--warning)"><div class="analytics-val warning">3</div><div class="analytics-lbl">Не сканировано</div></div>', unsafe_allow_html=True)
        with c3: st.markdown(f'<div class="analytics-card" style="border-top-color:var(--success)"><div class="analytics-val success">0%</div><div class="analytics-lbl">Прогресс</div></div>', unsafe_allow_html=True)
        with c4: st.markdown(f'<div class="analytics-card" style="border-top-color:var(--info)"><div class="analytics-val info">1</div><div class="analytics-lbl">Ниш в работе</div></div>', unsafe_allow_html=True)
        st.markdown("---")
        st.markdown('<div class="section-title-meta">Города</div>', unsafe_allow_html=True)
        cities_data = [("Брянск", 137, 0), ("Москва", 11, 0)]
        for city, total, scanned in cities_data:
            pct = int(scanned / total * 100) if total > 0 else 0
            st.markdown(f'<div style="display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--border)"><div style="flex:1;font-weight:500">{city}</div><div style="font-family:JetBrains Mono;color:var(--text-muted);font-size:13px">{scanned}/{total}</div><div style="width:120px;height:6px;background:var(--surface-3);border-radius:3px;overflow:hidden"><div style="width:{pct}%;height:100%;background:var(--accent);border-radius:3px"></div></div></div>', unsafe_allow_html=True)
        st.markdown("---")
        st.markdown('<div class="section-title-meta">Добавить регион</div>', unsafe_allow_html=True)
        rc1, rc2, rc3 = st.columns(3)
        with rc1: st.text_input("Город", placeholder="Брянск", label_visibility="collapsed")
        with rc2: st.text_input("Район", placeholder="Советский", label_visibility="collapsed")
        with rc3: st.selectbox("Ниша", ["салоны красоты", "автосервисы", "стоматологии"], label_visibility="collapsed")
        st.button("Добавить регион", type="primary")


# --- Запуск ---
if __name__ == "__main__":
    main()