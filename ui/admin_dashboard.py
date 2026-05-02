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
import json
import re
import urllib.parse
import subprocess
from typing import Optional

# Добавляем корень проекта в путь для импортов
sys.path.insert(0, str(Path(__file__).parent.parent))

# --- Конфигурация страницы ---
st.set_page_config(
    page_title="KURSOR Command Center",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Пути к данным ---
DB_PATH = Path("data/leads.db")
SCRAPED_DATA_DIR = Path("data")
CURATED_DATA_DIR = Path("data/curated")
YANDEX_DATA_DIR = Path("data/yandex")
EXTRACTED_DATA_DIR = Path("data/extracted")
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
    return CURATED_DATA_DIR / f"{slug}.json"


def get_extracted_file_path(lead_name: str, lead_id: int) -> Path:
    slug = slugify_name(lead_name, lead_id)
    return EXTRACTED_DATA_DIR / f"{slug}.json"


def get_content_status(lead_name: str, lead_id: int) -> str:
    error_key = f"curator_error_{lead_id}"
    if st.session_state.get(error_key):
        return "❌ Ошибка"
    return "🟢 Готов" if get_curated_file_path(lead_name, lead_id).exists() else "⚪ Не создан"


def get_yandex_file_path(lead_name: str, lead_id: int) -> Path:
    slug = slugify_name(lead_name, lead_id)
    return YANDEX_DATA_DIR / f"{slug}.json"


def get_yandex_status(lead_name: str, lead_id: int) -> str:
    return "🟢 Обогащён" if get_yandex_file_path(lead_name, lead_id).exists() else "⚪ Не обогащён"


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
    return NEURALSYNC_CONFIGS_DIR / f"{slug}.config.js"


def get_site_config_status(lead_name: str, lead_id: int) -> str:
    return "🟢 Собран" if get_site_config_path(lead_name, lead_id).exists() else "⚪ Не собран"


def run_config_builder(lead_id: int, no_copy: bool = False) -> tuple[bool, str]:
    try:
        cmd = ["python", "config_builder.py", str(lead_id)]
        if no_copy:
            cmd.append("--no-copy")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent),
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return result.returncode == 0, output.strip()
    except Exception as exc:
        return False, str(exc)


def run_block_extractor(lead_id: int) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["python", "block_extractor.py", str(lead_id)],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent),
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return result.returncode == 0, output.strip()
    except Exception as exc:
        return False, str(exc)


def run_config_builder_batch(lead_ids: list[int]) -> tuple[list[int], dict[int, str]]:
    success_ids: list[int] = []
    failures: dict[int, str] = {}
    for lead_id in lead_ids:
        ok, out = run_config_builder(lead_id)
        if ok:
            success_ids.append(lead_id)
        else:
            failures[lead_id] = out or "Ошибка"
    return success_ids, failures


def run_yandex_enricher(lead_id: int) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["python", "yandex_enricher.py", str(lead_id)],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent),
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return result.returncode == 0, output.strip()
    except Exception as exc:
        return False, str(exc)


def run_content_curator(lead_id: int) -> tuple[bool, str]:
    """Запускает content_curator.py для одного лида и возвращает (ok, output)."""
    try:
        result = subprocess.run(
            ["python", "content_curator.py", str(lead_id)],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent),
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return result.returncode == 0, output.strip()
    except Exception as exc:
        return False, str(exc)


def run_content_curator_with_status(lead_id: int) -> tuple[bool, str]:
    """Запускает content_curator.py с живым прогрессом через st.status()."""
    STAGE_MAP = (
        ("Готовим слоган", "✨ Готовлю слоган..."),
        ("Слоган готов", "✨ Слоган готов"),
        ("Готовим блок 'О нас'", "📝 Пишу блок «О нас»..."),
        ("Блок 'О нас' готов", "📝 Блок «О нас» готов"),
        ("Отбираем и редактируем отзывы", "⭐ Отбираю и чищу отзывы..."),
        ("Отобрано отзывов", "⭐ Отзывы готовы"),
        ("Формируем услуги", "🛍 Формирую услуги..."),
        ("Услуги готовы", "🛍 Услуги готовы"),
        ("Генерируем FAQ", "❓ Генерирую FAQ..."),
        ("FAQ готов", "❓ FAQ готов"),
        ("Curated JSON", "✅ Сохраняю результат..."),
        ("Курация завершена", "✅ Готово!"),
    )

    all_output: list[str] = []
    ok = False
    try:
        with st.status("⏳ Content Curator работает... обычно занимает 10–30 сек", expanded=True) as status_box:
            status_box.write("🔍 Читаю данные лида...")
            try:
                proc = subprocess.Popen(
                    ["python", "-u", "content_curator.py", str(lead_id)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=str(Path(__file__).parent),
                    encoding="utf-8",
                    errors="replace",
                )
                for raw_line in proc.stdout:
                    line = raw_line.strip()
                    if not line:
                        continue
                    all_output.append(line)
                    for keyword, stage_label in STAGE_MAP:
                        if keyword in line:
                            status_box.write(stage_label)
                            break
                proc.wait()
                ok = proc.returncode == 0
            except Exception as exc:
                all_output.append(str(exc))
                ok = False

            if ok:
                status_box.update(label="✅ Курация завершена!", state="complete", expanded=False)
            else:
                status_box.update(label="❌ Ошибка курации", state="error", expanded=True)
    except Exception as outer_exc:
        st.error(f"❌ Ошибка запуска куратора: {outer_exc}")
        all_output.append(str(outer_exc))

    return ok, "\n".join(all_output)


def render_curated_content(curated: dict, lead_id: int) -> None:
    about_text = str(curated.get("about", "") or "")
    tagline = str((curated.get("meta") or {}).get("tagline", "") or "")
    reviews = curated.get("reviews") if isinstance(curated.get("reviews"), list) else []
    faq = curated.get("faq") if isinstance(curated.get("faq"), list) else []
    services = curated.get("services") if isinstance(curated.get("services"), list) else []

    st.markdown("#### 🏷 Tagline")
    st.text_area("Слоган", value=tagline, height=70, key=f"tagline_{lead_id}")

    st.markdown("#### 📝 About")
    st.text_area("О нас", value=about_text, height=150, key=f"about_{lead_id}")

    st.markdown("#### ⭐ Reviews")
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

    st.markdown("#### ❓ FAQ")
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

    st.markdown("#### 🛍 Services")
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
        st.markdown("#### ⭐ Отзывы с Яндекс")
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
                    cols[i % 4].image(url, use_container_width=True)


# --- CSS стили для красивого отображения ---
st.markdown("""
<style>
    /* Заголовок */
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1f77b4;
        margin-bottom: 1rem;
    }
    /* Метрики */
    [data-testid="stMetricValue"] {
        font-size: 2rem;
        font-weight: 600;
    }
    [data-testid="stMetricLabel"] {
        font-size: 1rem;
        font-weight: 500;
    }
    /* Таблица */
    .stDataFrame {
        font-size: 0.9rem;
    }
    /* Кликабельные ссылки */
    a.link-cell {
        color: #1f77b4;
        text-decoration: none;
    }
    a.link-cell:hover {
        text-decoration: underline;
    }
    /* Рейтинг звезды */
    .rating-high {
        color: #28a745;
        font-weight: bold;
    }
    .rating-mid {
        color: #ffc107;
        font-weight: bold;
    }
    .rating-low {
        color: #dc3545;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)


# --- Функции работы с БД ---
def load_leads() -> pd.DataFrame:
    """Загружает все лиды из БД в DataFrame (каждый раз новое соединение)"""
    if not DB_PATH.exists():
        return pd.DataFrame()

    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT id, name, google_rating, reviews_count, address, phone, website, google_maps_url, emails, social_links, status, tech_score, load_time_sec, audit_notes, pitch_text, site_config_path, category FROM leads ORDER BY id DESC",
            conn
        )
    return df


def get_stats(df: pd.DataFrame) -> dict:
    """Вычисляет статистику для KPI"""
    if df.empty:
        return {
            "total": 0,
            "new_count": 0,
            "avg_rating": 0.0,
            "total_reviews": 0
        }

    new_count = len(df[df["status"] == "new"])
    avg_rating = df["google_rating"].mean() if df["google_rating"].notna().any() else 0.0
    total_reviews = df["reviews_count"].sum() if df["reviews_count"].notna().any() else 0

    return {
        "total": len(df),
        "new_count": new_count,
        "avg_rating": round(avg_rating, 2),
        "total_reviews": int(total_reviews)
    }


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


# --- Главная страница ---
def main():
    st.markdown('<p class="main-header">🎯 KURSOR Command Center</p>', unsafe_allow_html=True)
    st.markdown("### Пульт управления базой лидов")
    st.divider()

    df = load_leads()
    stats = get_stats(df)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(label="📊 Всего лидов", value=stats["total"], delta=None)
    with col2:
        st.metric(label="🆕 Новых (new)", value=stats["new_count"], delta=None)
    with col3:
        st.metric(label="⭐ Средний рейтинг", value=f"{stats['avg_rating']:.1f}", delta=None)
    with col4:
        st.metric(label="📝 Всего отзывов", value=f"{stats['total_reviews']:,}", delta=None)

    st.divider()
    st.subheader("📋 База лидов")

    if df.empty:
        st.info("📭 База данных пуста. Запустите Радар для поиска лидов.")
        return

    col_filter1, col_filter2, col_filter3, col_filter4 = st.columns([1, 1, 1, 1])
    with col_filter1:
        status_filter = st.multiselect(
            "Фильтр по статусу:",
            options=df["status"].unique().tolist(),
            default=df["status"].unique().tolist(),
        )
    with col_filter2:
        all_categories = df["category"].dropna().unique().tolist() if "category" in df.columns else []
        category_options = ["Все"] + sorted(all_categories)
        category_filter = st.selectbox("Фильтр по категории:", options=category_options, index=0)
    with col_filter3:
        rating_min = st.slider("Минимальный рейтинг:", min_value=0.0, max_value=5.0, value=0.0, step=0.1)
    with col_filter4:
        content_filter = st.selectbox(
            "Контент:",
            options=["Все", "🟢 Готов", "⚪ Не создан"],
            index=0,
            key="content_filter_select",
        )

    df_filtered = df[df["status"].isin(status_filter)]
    if category_filter != "Все":
        df_filtered = df_filtered[df_filtered["category"] == category_filter]
    df_filtered = df_filtered[df_filtered["google_rating"] >= rating_min]

    if content_filter == "🟢 Готов":
        df_filtered = df_filtered[
            df_filtered.apply(
                lambda row: get_curated_file_path(str(row.get("name", "")), int(row.get("id", 0))).exists(),
                axis=1,
            )
        ]
    elif content_filter == "⚪ Не создан":
        df_filtered = df_filtered[
            ~df_filtered.apply(
                lambda row: get_curated_file_path(str(row.get("name", "")), int(row.get("id", 0))).exists(),
                axis=1,
            )
        ]

    hide_perfect = st.session_state.get("hide_perfect", True)
    max_score_filter = st.session_state.get("max_score_filter", 85)
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

    df_filtered = df_filtered.copy()
    df_filtered["_sort_key"] = df_filtered["tech_score"].fillna(999)
    df_filtered = df_filtered.sort_values("_sort_key", ascending=True).drop(columns=["_sort_key"])

    lead_options = [f"{int(row['id'])} — {row['name']}" for _, row in df_filtered.iterrows()]
    valid_ids = set(df_filtered["id"].astype(int).tolist())
    if "selected_lead_id" not in st.session_state or int(st.session_state["selected_lead_id"]) not in valid_ids:
        st.session_state["selected_lead_id"] = int(df_filtered.iloc[0]["id"])

    tab_base, tab_pipeline = st.tabs(["📊 База лидов", "🎨 Контент-конвейер"])

    with tab_base:
        h1, h2 = st.columns([3, 1])
        with h1:
            st.subheader("📊 Все лиды")
            st.write(f"Найдено: **{len(df_filtered)}** лидов")
        with h2:
            batch_config_ids = [
                int(row["id"])
                for _, row in df_filtered.iterrows()
                if get_curated_file_path(str(row.get("name", "")), int(row.get("id", 0))).exists()
                and get_yandex_file_path(str(row.get("name", "")), int(row.get("id", 0))).exists()
                and not get_site_config_path(str(row.get("name", "")), int(row.get("id", 0))).exists()
            ]
            if st.button(f"📦 Сгенерировать конфиги ({len(batch_config_ids)})", use_container_width=True, disabled=len(batch_config_ids) == 0):
                with st.spinner("Собираю конфиги..."):
                    ok_ids, err_map = run_config_builder_batch(batch_config_ids)
                if ok_ids:
                    st.success(f"✅ Собрано конфигов: {len(ok_ids)}")
                if err_map:
                    st.error(f"❌ Ошибок: {len(err_map)}")
                    for bid, bout in list(err_map.items())[:3]:
                        st.code(f"ID={bid}\n{bout[:1800]}", language="")
                st.rerun()

        def normalize_url_for_table(value: object) -> object:
            if value is None or (isinstance(value, float) and pd.isna(value)):
                return None
            raw = str(value).strip()
            if not raw:
                return None
            if raw.lower().startswith(("http://", "https://")):
                return raw
            return None

        display_cols = ["id", "name", "category", "google_rating", "reviews_count", "tech_score", "website", "google_maps_url", "status"]
        display_cols = [c for c in display_cols if c in df_filtered.columns]
        df_table = df_filtered[display_cols].copy()

        df_table["content_status"] = df_table.apply(
            lambda row: get_content_status(str(row.get("name", "")), int(row.get("id", 0))),
            axis=1,
        )
        df_table["yandex_status"] = df_table.apply(
            lambda row: get_yandex_status(str(row.get("name", "")), int(row.get("id", 0))),
            axis=1,
        )
        df_table["config_status"] = df_table.apply(
            lambda row: get_site_config_status(str(row.get("name", "")), int(row.get("id", 0))),
            axis=1,
        )
        df_table["yandex_maps_url"] = df_table.apply(
            lambda row: get_yandex_maps_url(str(row.get("name", "")), int(row.get("id", 0))),
            axis=1,
        )
        df_table["two_gis_url"] = df_table.apply(
            lambda row: (
                f"https://2gis.ru/search/{urllib.parse.quote_plus(str(row.get('address') or '').strip())}"
                if str(row.get("address") or "").strip()
                else None
            ),
            axis=1,
        )

        df_table["google_rating"] = df_table["google_rating"].apply(lambda x: f"⭐ {x:.1f}" if pd.notna(x) else "—")
        df_table["tech_score"] = df_table["tech_score"].apply(lambda x: f"{int(x)}" if pd.notna(x) else "—")
        if "category" in df_table.columns:
            df_table["category"] = df_table["category"].fillna("other")

        df_table["website"] = df_table["website"].apply(normalize_url_for_table)
        df_table["google_maps_url"] = df_table["google_maps_url"].apply(add_hl_ru).apply(normalize_url_for_table)
        df_table["yandex_maps_url"] = df_table["yandex_maps_url"].apply(normalize_url_for_table)
        df_table["two_gis_url"] = df_table["two_gis_url"].apply(normalize_url_for_table)

        df_table = df_table.rename(
            columns={
                "id": "ID",
                "name": "Название",
                "category": "Категория",
                "google_rating": "Рейтинг",
                "reviews_count": "Отзывы",
                "tech_score": "Score",
                "website": "Сайт",
                "google_maps_url": "Карты",
                "status": "Статус",
                "content_status": "Контент",
                "yandex_status": "Яндекс",
                "config_status": "📦 Конфиг",
                "yandex_maps_url": "Я.Maps",
                "two_gis_url": "2GIS",
            }
        )

        st.dataframe(
            df_table,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Сайт": st.column_config.LinkColumn("Сайт", width="small"),
                "Карты": st.column_config.LinkColumn("Карты", width="small"),
                "Я.Maps": st.column_config.LinkColumn("Я.Maps", width="small"),
                "2GIS": st.column_config.LinkColumn("2GIS", width="small"),
                "Название": st.column_config.TextColumn("Название", width="large"),
                "Категория": st.column_config.TextColumn("Категория", width="small"),
                "Score": st.column_config.TextColumn("Score", width="small"),
                "Статус": st.column_config.TextColumn("Статус", width="small"),
                "Контент": st.column_config.TextColumn("Контент", width="small"),
                "Яндекс": st.column_config.TextColumn("Яндекс", width="small"),
                "📦 Конфиг": st.column_config.TextColumn("📦 Конфиг", width="small"),
                "Рейтинг": st.column_config.TextColumn("Рейтинг", width="small"),
                "Отзывы": st.column_config.NumberColumn("Отзывы", width="small"),
                "ID": st.column_config.NumberColumn("ID", width="small"),
            },
        )

        c1, c2 = st.columns([3, 1])
        with c1:
            table_selected_label = st.selectbox("Открыть лид в конвейере:", options=lead_options, key="base_lead_selector")
        with c2:
            if st.button("➡ Перейти", use_container_width=True):
                st.session_state["selected_lead_id"] = int(table_selected_label.split(" — ")[0])
                st.success("Лид выбран. Перейдите на вкладку «🎨 Контент-конвейер»")

    with tab_pipeline:
        selected_idx = next(
            (i for i, lbl in enumerate(lead_options) if int(lbl.split(" — ")[0]) == int(st.session_state["selected_lead_id"])),
            0,
        )
        selected_label = st.selectbox(
            "Выбрать лида для работы:",
            options=lead_options,
            index=selected_idx,
            key="pipeline_lead_selector",
        )
        selected_lead_id = int(selected_label.split(" — ")[0])
        st.session_state["selected_lead_id"] = selected_lead_id

        selected_row = df_filtered[df_filtered["id"].astype(int) == selected_lead_id].iloc[0]
        selected_name = str(selected_row.get("name", f"Lead {selected_lead_id}"))
        selected_curated_path = get_curated_file_path(selected_name, selected_lead_id)
        selected_ya_path = get_yandex_file_path(selected_name, selected_lead_id)
        selected_extracted_path = get_extracted_file_path(selected_name, selected_lead_id)
        selected_cfg_path = get_site_config_path(selected_name, selected_lead_id)
        selected_ya_url = get_yandex_maps_url(selected_name, selected_lead_id)

        yandex_rating = "—"
        if selected_ya_path.exists():
            try:
                with open(selected_ya_path, "r", encoding="utf-8") as f:
                    yandex_rating = (json.load(f).get("yandex", {}) or {}).get("rating") or "—"
            except Exception:
                yandex_rating = "—"

        st.markdown("### 🧾 Карточка лида")
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("Название", selected_name)
        with m2:
            google_val = selected_row.get("google_rating")
            st.metric("Рейтинг Google", f"⭐ {google_val:.1f}" if pd.notna(google_val) else "—")
        with m3:
            st.metric("Рейтинг Яндекс", f"⭐ {yandex_rating}" if yandex_rating != "—" else "—")
        with m4:
            st.metric("Телефон", selected_row.get("phone") or "—")
        st.caption(f"📍 {selected_row.get('address') or '—'}")

        audit_status = "🟢 Аудит" if pd.notna(selected_row.get("tech_score")) else "⚪ Аудит"
        content_status = get_content_status(selected_name, selected_lead_id)
        yandex_status = get_yandex_status(selected_name, selected_lead_id)
        config_status = get_site_config_status(selected_name, selected_lead_id)
        pitch_status = "🟢 Питч" if pd.notna(selected_row.get("pitch_text")) and str(selected_row.get("pitch_text")).strip() else "⚪ Питч"
        st.markdown(f"{audit_status}  |  🎨 {content_status}  |  🗺 {yandex_status}  |  📦 {config_status}  |  ✉ {pitch_status}")

        links = []
        if selected_row.get("website"):
            links.append(f"[🌐 Сайт]({selected_row.get('website')})")
        if selected_row.get("google_maps_url"):
            links.append(f"[🗺 Google]({add_hl_ru(str(selected_row.get('google_maps_url')))})")
        if selected_ya_url:
            links.append(f"[🧭 Я.Maps]({selected_ya_url})")
        if selected_row.get("address"):
            links.append(f"[📍 2GIS](https://2gis.ru/search/{urllib.parse.quote_plus(str(selected_row.get('address')))})")
        if links:
            st.markdown(" | ".join(links))

        with st.expander("🎨 Контент", expanded=False):
            st.caption(f"Файл: `{selected_curated_path}`")
            content_exists = selected_curated_path.exists()
            if content_exists:
                cv1, cv2 = st.columns(2)
                with cv1:
                    if st.button("👁 Просмотреть контент", key="curator_view_btn_single", use_container_width=True):
                        st.session_state["curator_show_content"] = selected_lead_id
                with cv2:
                    if st.button("🔄 Перегенерировать", key="curator_regen_btn_single", use_container_width=True):
                        try:
                            selected_curated_path.unlink(missing_ok=True)
                        except Exception as exc:
                            st.error(f"❌ Не удалось удалить JSON: {exc}")
                        else:
                            ok_regen, out_regen = run_content_curator_with_status(selected_lead_id)
                            if ok_regen:
                                st.session_state.pop(f"curator_error_{selected_lead_id}", None)
                                st.session_state["curator_show_content"] = selected_lead_id
                            else:
                                st.session_state[f"curator_error_{selected_lead_id}"] = out_regen or "Ошибка"
                            st.rerun()
            else:
                if st.button("🎨 Сгенерировать контент", key="curator_curate_btn_single", type="primary"):
                    ok_cur, out_cur = run_content_curator_with_status(selected_lead_id)
                    if ok_cur:
                        st.session_state.pop(f"curator_error_{selected_lead_id}", None)
                        st.session_state["curator_show_content"] = selected_lead_id
                    else:
                        st.session_state[f"curator_error_{selected_lead_id}"] = out_cur or "Ошибка"
                    st.rerun()
            if st.session_state.get(f"curator_error_{selected_lead_id}"):
                st.code(st.session_state[f"curator_error_{selected_lead_id}"][:3000], language="")
            if st.session_state.get("curator_show_content") == selected_lead_id and content_exists:
                try:
                    with open(selected_curated_path, "r", encoding="utf-8") as f:
                        render_curated_content(json.load(f), selected_lead_id)
                except Exception as exc:
                    st.error(f"❌ Ошибка чтения curated JSON: {exc}")

        with st.expander("🗺 Yandex", expanded=False):
            st.caption(f"Файл: `{selected_ya_path}`")
            ya_exists = selected_ya_path.exists()
            if ya_exists:
                yv1, yv2 = st.columns(2)
                with yv1:
                    if st.button("👁 Просмотреть данные Яндекс", key="ya_view_btn_single", use_container_width=True):
                        st.session_state["ya_show_data"] = selected_lead_id
                with yv2:
                    if st.button("🔄 Перегенерировать", key="ya_regen_btn_single", use_container_width=True):
                        try:
                            selected_ya_path.unlink(missing_ok=True)
                        except Exception as ya_exc:
                            st.error(f"❌ Не удалось удалить JSON: {ya_exc}")
                        else:
                            ok_ya_r, out_ya_r = run_yandex_enricher(selected_lead_id)
                            if not ok_ya_r:
                                st.session_state[f"ya_error_{selected_lead_id}"] = out_ya_r or "Ошибка"
                            st.rerun()
            else:
                if st.button("🗺 Обогатить с Яндекса", key="ya_enrich_btn_single", type="primary"):
                    ok_ya_s, out_ya_s = run_yandex_enricher(selected_lead_id)
                    if not ok_ya_s:
                        st.session_state[f"ya_error_{selected_lead_id}"] = out_ya_s or "Ошибка"
                    st.rerun()
            if st.session_state.get(f"ya_error_{selected_lead_id}"):
                st.code(st.session_state[f"ya_error_{selected_lead_id}"][:3000], language="")
            if st.session_state.get("ya_show_data") == selected_lead_id and ya_exists:
                try:
                    with open(selected_ya_path, "r", encoding="utf-8") as f:
                        render_yandex_data(json.load(f), selected_lead_id)
                except Exception as exc:
                    st.error(f"❌ Ошибка чтения Yandex JSON: {exc}")

        with st.expander("🔍 Парсинг блоков с сайта", expanded=False):
            st.caption(f"Файл: `{selected_extracted_path}`")
            extracted_exists = selected_extracted_path.exists()

            if extracted_exists:
                ex1, ex2 = st.columns(2)
                with ex1:
                    if st.button("👁 Просмотреть", key="extract_view_btn_single", use_container_width=True):
                        st.session_state["extract_show_data"] = selected_lead_id
                with ex2:
                    if st.button("🔄 Перезапустить", key="extract_rerun_btn_single", use_container_width=True):
                        ok_ex_r, out_ex_r = run_block_extractor(selected_lead_id)
                        if ok_ex_r:
                            st.session_state.pop(f"extract_error_{selected_lead_id}", None)
                            st.session_state["extract_show_data"] = selected_lead_id
                        else:
                            st.session_state[f"extract_error_{selected_lead_id}"] = out_ex_r or "Ошибка"
                        st.rerun()
            else:
                if st.button("🔍 Извлечь блоки", key="extract_run_btn_single", type="primary"):
                    ok_ex_s, out_ex_s = run_block_extractor(selected_lead_id)
                    if ok_ex_s:
                        st.session_state.pop(f"extract_error_{selected_lead_id}", None)
                        st.session_state["extract_show_data"] = selected_lead_id
                    else:
                        st.session_state[f"extract_error_{selected_lead_id}"] = out_ex_s or "Ошибка"
                    st.rerun()

            if st.session_state.get(f"extract_error_{selected_lead_id}"):
                st.code(st.session_state[f"extract_error_{selected_lead_id}"][:3000], language="")

            if st.session_state.get("extract_show_data") == selected_lead_id and extracted_exists:
                try:
                    with open(selected_extracted_path, "r", encoding="utf-8") as f:
                        extracted_payload = json.load(f)
                    services = extracted_payload.get("serviceCarousel") if isinstance(extracted_payload.get("serviceCarousel"), list) else []
                    faq = extracted_payload.get("faq_accordion") if isinstance(extracted_payload.get("faq_accordion"), list) else []
                    st.info(f"Найдено: services={len(services)} | faq={len(faq)}")
                    if services:
                        with st.expander(f" Услуги ({len(services)})"):
                            for i, item in enumerate(services[:12], start=1):
                                title = str(item.get("name") or item.get("title") or "") if isinstance(item, dict) else ""
                                price = str(item.get("price") or "") if isinstance(item, dict) else ""
                                desc = str(item.get("description") or "") if isinstance(item, dict) else ""
                                image = str(item.get("image") or item.get("image_url") or "") if isinstance(item, dict) else ""
                                st.text_area(
                                    f"Услуга {i}: {title}",
                                    value=f"Цена: {price}\nФото: {image}\nОписание: {desc}",
                                    height=120,
                                    key=f"extract_service_{selected_lead_id}_{i}",
                                )
                    if faq:
                        with st.expander(f" FAQ ({len(faq)})"):
                            for i, item in enumerate(faq[:12], start=1):
                                if not isinstance(item, dict):
                                    continue
                                q = str(item.get("q") or "")
                                a = str(item.get("a") or "")
                                st.text_area(
                                    f"FAQ {i}",
                                    value=f"Q: {q}\nA: {a}",
                                    height=120,
                                    key=f"extract_faq_{selected_lead_id}_{i}",
                                )
                except Exception as exc:
                    st.error(f"❌ Ошибка чтения extracted JSON: {exc}")

        with st.expander("📦 Конфиг сайта", expanded=False):
            st.caption(f"Файл: `{selected_cfg_path}`")
            cfg_exists = selected_cfg_path.exists()
            curated_exists = selected_curated_path.exists()
            yandex_exists = selected_ya_path.exists()

            missing_sources = []
            if not curated_exists:
                missing_sources.append("curated")
            if not yandex_exists:
                missing_sources.append("yandex")

            if cfg_exists:
                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button("👁 Просмотреть", key="cfg_view_btn_single", use_container_width=True):
                        st.session_state["cfg_show_data"] = selected_lead_id
                with cc2:
                    if st.button("🔄 Пересобрать", key="cfg_rebuild_btn_single", use_container_width=True):
                        ok_cfg_r, out_cfg_r = run_config_builder(selected_lead_id)
                        if ok_cfg_r:
                            st.session_state.pop(f"cfg_error_{selected_lead_id}", None)
                            st.session_state["cfg_show_data"] = selected_lead_id
                        else:
                            st.session_state[f"cfg_error_{selected_lead_id}"] = out_cfg_r or "Ошибка"
                        st.rerun()
            else:
                if missing_sources:
                    st.warning(f"Нельзя собрать конфиг: не готовы источники — {', '.join(missing_sources)}")
                if st.button(
                    "📦 Собрать конфиг",
                    key="cfg_build_btn_single",
                    type="primary",
                    disabled=bool(missing_sources),
                ):
                    ok_cfg_b, out_cfg_b = run_config_builder(selected_lead_id)
                    if ok_cfg_b:
                        st.session_state.pop(f"cfg_error_{selected_lead_id}", None)
                        st.session_state["cfg_show_data"] = selected_lead_id
                    else:
                        st.session_state[f"cfg_error_{selected_lead_id}"] = out_cfg_b or "Ошибка"
                    st.rerun()

            if st.session_state.get(f"cfg_error_{selected_lead_id}"):
                st.code(st.session_state[f"cfg_error_{selected_lead_id}"][:3000], language="")

            if st.session_state.get("cfg_show_data") == selected_lead_id and cfg_exists:
                size_kb = selected_cfg_path.stat().st_size / 1024 if selected_cfg_path.exists() else 0
                st.info(f"Путь: `{selected_cfg_path}` | Размер: {size_kb:.1f} KB")

            st.caption(
                "После сборки выполните в репозитории neuralsync: "
                "git add . && git commit -m 'Add <slug> config' && git push. "
                "Vercel передеплоит автоматически."
            )

        with st.expander("✉ Pitch", expanded=False):
            pitch_text = str(selected_row.get("pitch_text") or "").strip()
            if not pitch_text:
                st.info("📭 Для этого лида пока нет питча. Запустите Нейро-Сценариста в боковой панели.")
            else:
                score = selected_row.get("tech_score")
                score_icon = "❌" if pd.notna(score) and score < 50 else "⚠️" if pd.notna(score) and score < 80 else "✅"
                maps_url = add_hl_ru(selected_row["google_maps_url"]) if pd.notna(selected_row.get("google_maps_url")) and selected_row.get("google_maps_url") else None
                maps_link = f"[📍 Карты]({maps_url})" if maps_url else "📍 Карты: —"
                rating_value = selected_row.get("google_rating")
                rating_text = f"⭐ {rating_value:.1f}" if pd.notna(rating_value) else "—"
                st.markdown(
                    f"**Score:** {score_icon} {int(score) if pd.notna(score) else '—'}  |  "
                    f"**Рейтинг:** {rating_text}  |  "
                    f"**Сайт:** {selected_row.get('website') or '—'}  |  {maps_link}"
                )
                st.text_area("Текст питча:", value=pitch_text, height=260, label_visibility="collapsed")

                send_links = build_send_links(pitch_text, selected_row)
                pb1, pb2, pb3 = st.columns(3)
                with pb1:
                    if send_links.get("whatsapp"):
                        st.link_button("💬 WhatsApp", send_links["whatsapp"], use_container_width=True)
                    else:
                        st.caption("💬 WhatsApp: нет телефона")
                with pb2:
                    if send_links.get("telegram"):
                        st.link_button("✈️ Telegram", send_links["telegram"], use_container_width=True)
                    else:
                        st.caption("✈️ Telegram: нет ссылки")
                with pb3:
                    if send_links.get("email"):
                        st.link_button("📧 Email", send_links["email"], use_container_width=True)
                    else:
                        st.caption("📧 Email: нет почты")

                if st.button("🔄 Перегенерировать питч", key=f"regen_{selected_lead_id}"):
                    with st.spinner("🤖 Генерация нового варианта..."):
                        try:
                            from services.pitch_builder import generate_single_pitch
                            result = generate_single_pitch(int(selected_lead_id))
                            if result:
                                st.success(f"✅ Новый питч для **{result['name']}** сгенерирован!")
                                st.cache_resource.clear()
                                st.rerun()
                            else:
                                st.error("❌ Не удалось сгенерировать питч")
                        except Exception as e:
                            st.error(f"❌ Ошибка: {e}")

                st.markdown("### 🌐 Генерация сайта")
                site_config_exists = pd.notna(selected_row.get("site_config_path")) and selected_row.get("site_config_path")
                if site_config_exists and Path(str(selected_row["site_config_path"])).exists():
                    st.success(f"✅ JSON уже сгенерирован: `{selected_row['site_config_path']}`")
                    try:
                        with open(str(selected_row["site_config_path"]), "r", encoding="utf-8") as f:
                            config_data = json.load(f)
                        with st.expander("📄 Просмотр JSON"):
                            st.code(json.dumps(config_data, ensure_ascii=False, indent=2), language="json")
                    except Exception:
                        pass
                else:
                    if st.button("🌐 Сгенерировать сайт (JSON)", key=f"gen_site_{selected_lead_id}"):
                        with st.spinner("🤖 Генерация JSON конфигурации сайта..."):
                            try:
                                from services.site_config_generator import generate_site_config
                                scraped_data = load_scraped_data_for_lead(selected_lead_id)
                                result = generate_site_config(selected_lead_id, scraped_data)
                                if result:
                                    st.success(f"✅ JSON сайта сгенерирован для **{result['name']}**!")
                                    st.info(f"📁 Файл: `{result['file_path']}`")
                                    with st.expander("📄 Просмотр JSON"):
                                        st.code(json.dumps(result["config"], ensure_ascii=False, indent=2), language="json")
                                    st.cache_resource.clear()
                                    st.rerun()
                                else:
                                    st.error("❌ Не удалось сгенерировать JSON сайта")
                            except Exception as e:
                                st.error(f"❌ Ошибка: {e}")

                st.markdown("*💡 Выделите текст мышкой выше и скопируйте через Ctrl+C*")


# --- Sidebar (Панель управления) ---
def sidebar():
    with st.sidebar:
        st.markdown("## 🎛️ Панель управления")
        st.divider()

        # --- Форма Радара ---
        st.markdown("### 🎯 Радар (Поиск)")

        with st.form("radar_form"):
            query = st.text_input(
                "Запрос для Радара:",
                value="частная стоматология Китай-город",
                placeholder="Введите поисковый запрос..."
            )

            max_pages = st.number_input(
                "Макс. страниц:",
                min_value=1,
                max_value=10,
                value=3
            )

            radar_category = st.text_input(
                "Категория (ниша) для сохранения:",
                value="beauty",
                help="Категория будет записана всем лидам, найденным в этом запуске Радара"
            )

            radar_submitted = st.form_submit_button("🚀 Запустить Радар", type="primary")

            if radar_submitted:
                if not query or not query.strip():
                    st.error("❌ Введите поисковый запрос!")
                else:
                    # Загружаем API ключ
                    import os
                    from dotenv import load_dotenv
                    load_dotenv()
                    api_key = os.getenv("GOOGLE_PLACES_API_KEY")

                    if not api_key:
                        st.error("❌ GOOGLE_PLACES_API_KEY не найден в .env файле!")
                    else:
                        st.info(f"🎯 Радар запущен: **{query}**")
                        
                        # Контейнер для логов в реальном времени
                        log_container = st.empty()
                        radar_logs = []

                        async def log_to_ui(message: str):
                            radar_logs.append(message)
                            # Показываем последние 15 строк логов
                            recent = radar_logs[-15:]
                            log_container.code("\n".join(recent), language="")

                        def run_radar():
                            """Обёртка для запуска async кода в Streamlit"""
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            try:
                                from services.google_radar import search_and_save, init_db

                                # Инициализируем БД
                                loop.run_until_complete(init_db())

                                # Запускаем парсер
                                leads = loop.run_until_complete(
                                    search_and_save(
                                        query=query,
                                        api_key=api_key,
                                        max_pages=max_pages,
                                        page_delay=2,
                                        log_callback=log_to_ui,
                                        category=radar_category
                                    )
                                )
                                return leads, None
                            except Exception as e:
                                import traceback
                                tb = traceback.format_exc()
                                safe_print(f"[ERROR] Критическая ошибка парсера: {str(e)}")
                                safe_print(f"[ERROR] Traceback: {tb}")
                                return None, (str(e), tb)
                            finally:
                                loop.close()

                        with st.spinner("⏳ Парсинг лидов..."):
                            leads, error = run_radar()

                            if error:
                                error_detail, tb = error
                                st.error(f"❌ Ошибка парсера: {error_detail}")
                                with st.expander("🔍 Полный traceback"):
                                    st.code(tb, language="")
                            else:
                                # Показываем результаты
                                st.success(f"✅ Радар завершён! Найдено **{len(leads)}** лидов.")
                                with_site = sum(1 for l in leads if l.website)
                                without_site = len(leads) - with_site
                                st.info(f"🌐 С сайтом: **{with_site}**, 🚫 Без сайта: **{without_site}**")

                                # Показываем все логи
                                with st.expander("📋 Полные логи"):
                                    st.code("\n".join(radar_logs), language="")

                                # Обновляем кшированные данные
                                st.cache_resource.clear()

        st.divider()

        # --- Форма Рентгена (Аудит) ---
        st.markdown("### 🔬 Рентген (Аудит)")

        with st.form("audit_form"):
            audit_submitted = st.form_submit_button("🔍 Запустить Рентген (Аудит)", type="secondary")

            if audit_submitted:
                with st.spinner("🔬 Аудит сайтов в процессе..."):
                    try:
                        from services.xray_auditor import run_xray_audit_sync
                        result = run_xray_audit_sync()

                        st.success(f"✅ Аудит завершён!")
                        st.metric("Проверено", result["total"])
                        st.metric("Успешно", result["audited"])
                        st.metric("Ошибок", result["errors"])

                        if result["results"]:
                            with st.expander("📋 Результаты аудита"):
                                for r in result["results"][:20]:  # Показываем первые 20
                                    icon = "✅" if r["status"] == "audited" else "❌"
                                    st.write(f"{icon} **ID {r['lead_id']}**: Score={r['tech_score']}, {r['audit_notes'][:60]}...")

                        # Обновляем данные
                        st.cache_resource.clear()

                    except Exception as e:
                        st.error(f"❌ Ошибка при аудите: {e}")

        st.divider()

        # --- Форма Нейро-Сценариста ---
        st.markdown("### 🤖 Нейро-Сценарист")

        with st.form("pitch_form"):
            pitch_submitted = st.form_submit_button("💌 Сгенерировать питчи", type="secondary")

            if pitch_submitted:
                with st.spinner("🤖 Генерация продающих сообщений..."):
                    try:
                        from services.pitch_builder import generate_pitches_sync
                        result = generate_pitches_sync()

                        st.success(f"✅ Генерация завершена!")
                        st.metric("Всего лидов", result["total"])
                        st.metric("Питчей создано", result["generated"])
                        st.metric("Ошибок", result["errors"])

                        if result["results"]:
                            with st.expander("💌 Сгенерированные питчи"):
                                for r in result["results"][:10]:
                                    st.markdown(f"**{r['name']}** (ID={r['lead_id']})")
                                    st.info(r['pitch'])
                                    st.divider()

                        # Обновляем данные
                        st.cache_resource.clear()

                    except Exception as e:
                        st.error(f"❌ Ошибка при генерации: {e}")

        st.divider()

        # --- Фильтры отображения ---
        st.markdown("### 🔍 Фильтры")

        # Чекбокс для скрытия идеальных
        st.session_state["hide_perfect"] = st.checkbox(
            "Скрыть сайты с оценкой 100",
            value=st.session_state.get("hide_perfect", True)
        )

        # Слайдер качества
        st.session_state["max_score_filter"] = st.slider(
            "Показывать сайты с оценкой не выше:",
            min_value=0,
            max_value=100,
            value=st.session_state.get("max_score_filter", 85),
            step=5
        )

        st.divider()

        # --- Управление данными ---
        st.markdown("### 🔄 Данные")

        if st.button("🔄 Обновить данные", type="secondary"):
            st.cache_resource.clear()
            st.rerun()

        st.divider()

        # --- Информация ---
        st.markdown("### ℹ️ Информация")
        st.markdown(f"""
        - **База данных:** `{DB_PATH}`
        - **Версия:** 1.1.0
        """)

        if st.button("🗑️ Очистить кэш"):
            st.cache_resource.clear()
            st.success("✅ Кэш очищен!")


# --- Запуск ---
if __name__ == "__main__":
    sidebar()
    main()