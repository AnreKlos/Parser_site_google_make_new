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
from typing import Optional

# Добавляем корень проекта в путь для импортов
sys.path.insert(0, str(Path(__file__).parent))

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
    # Заголовок
    st.markdown('<p class="main-header">🎯 KURSOR Command Center</p>', unsafe_allow_html=True)
    st.markdown("### Пульт управления базой лидов")
    st.divider()

    # Загрузка данных
    df = load_leads()
    stats = get_stats(df)

    # --- KPI Метрики ---
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            label="📊 Всего лидов",
            value=stats["total"],
            delta=None
        )

    with col2:
        st.metric(
            label="🆕 Новых (new)",
            value=stats["new_count"],
            delta=None
        )

    with col3:
        st.metric(
            label="⭐ Средний рейтинг",
            value=f"{stats['avg_rating']:.1f}",
            delta=None
        )

    with col4:
        st.metric(
            label="📝 Всего отзывов",
            value=f"{stats['total_reviews']:,}",
            delta=None
        )

    st.divider()

    # --- Таблица лидов ---
    st.subheader("📋 База лидов")

    if df.empty:
        st.info("📭 База данных пуста. Запустите Радар для поиска лидов.")
    else:
        # Фильтры
        col_filter1, col_filter2, col_filter3 = st.columns([1, 1, 2])

        with col_filter1:
            status_filter = st.multiselect(
                "Фильтр по статусу:",
                options=df["status"].unique().tolist(),
                default=df["status"].unique().tolist()
            )

        with col_filter2:
            # Фильтр по категории
            all_categories = df["category"].dropna().unique().tolist() if "category" in df.columns else []
            category_options = ["Все"] + sorted(all_categories)
            category_filter = st.selectbox(
                "Фильтр по категории:",
                options=category_options,
                index=0
            )

        with col_filter3:
            rating_min = st.slider(
                "Минимальный рейтинг:",
                min_value=0.0,
                max_value=5.0,
                value=0.0,
                step=0.1
            )

        # Применяем фильтры
        df_filtered = df[df["status"].isin(status_filter)]
        if category_filter != "Все":
            df_filtered = df_filtered[df_filtered["category"] == category_filter]
        df_filtered = df_filtered[df_filtered["google_rating"] >= rating_min]

        # --- Фильтры из sidebar ---
        hide_perfect = st.session_state.get("hide_perfect", True)
        max_score_filter = st.session_state.get("max_score_filter", 85)

        # Скрыть идеальные (score == 100)
        if hide_perfect:
            df_filtered = df_filtered[
                (df_filtered["tech_score"] != 100) |
                (df_filtered["tech_score"].isna())
            ]

        # Фильтр по максимальному score (оставляем new с пустым score)
        df_filtered = df_filtered[
            (df_filtered["tech_score"] <= max_score_filter) |
            (df_filtered["tech_score"].isna()) |
            (df_filtered["status"] == "new")
        ]

        # --- Сортировка: проблемные вверху ---
        df_filtered["_sort_key"] = df_filtered["tech_score"].fillna(999)
        df_filtered = df_filtered.sort_values("_sort_key", ascending=True)
        df_filtered = df_filtered.drop(columns=["_sort_key"])

        # --- Таблица лидов (без питчей, только данные) ---
        st.subheader("📊 Все лиды")
        st.write(f"Найдено: **{len(df_filtered)}** лидов")

        # Подготавливаем данные — БЕЗ pitch_text
        display_cols = ["id", "name", "category", "google_rating", "reviews_count", "tech_score", "website", "google_maps_url", "status"]
        # Оставляем только те колонки, что есть в датафрейме
        display_cols = [c for c in display_cols if c in df_filtered.columns]
        df_table = df_filtered[display_cols].copy()

        # Форматируем колонки
        df_table["google_rating"] = df_table["google_rating"].apply(lambda x: f"⭐ {x:.1f}" if pd.notna(x) else "—")
        df_table["tech_score"] = df_table["tech_score"].apply(lambda x: f"{int(x)}" if pd.notna(x) else "—")
        if "category" in df_table.columns:
            df_table["category"] = df_table["category"].fillna("other")

        # Добавляем hl=ru к URL карт
        df_table["google_maps_url"] = df_table["google_maps_url"].apply(add_hl_ru)

        # Формируем названия колонок
        col_rename = {
            "id": "ID",
            "name": "Название",
            "category": "Категория",
            "google_rating": "Рейтинг",
            "reviews_count": "Отзывы",
            "tech_score": "Score",
            "website": "Сайт",
            "google_maps_url": "Карты",
            "status": "Статус",
        }
        df_table = df_table.rename(columns=col_rename)

        # Чистая таблица без интерактивности — ничего не прыгает
        st.dataframe(
            df_table,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Сайт": st.column_config.LinkColumn("Сайт", width="small"),
                "Карты": st.column_config.LinkColumn("Карты", width="small"),
                "Название": st.column_config.TextColumn("Название", width="large"),
                "Категория": st.column_config.TextColumn("Категория", width="small"),
                "Score": st.column_config.TextColumn("Score", width="small"),
                "Статус": st.column_config.TextColumn("Статус", width="small"),
                "Рейтинг": st.column_config.TextColumn("Рейтинг", width="small"),
                "Отзывы": st.column_config.NumberColumn("Отзывы", width="small"),
                "ID": st.column_config.NumberColumn("ID", width="small"),
            }
        )

        st.divider()

        # --- Селектор для выбора компании с питчем ---
        pitched_companies = df_filtered[
            df_filtered["pitch_text"].notna() & (df_filtered["pitch_text"] != "")
        ].copy()

        if not pitched_companies.empty:
            company_options = pitched_companies["name"].tolist()
            # Сбрасываем селектор при изменении фильтров
            if "selected_company" not in st.session_state:
                st.session_state["selected_company"] = company_options[0]

            selected_company = st.selectbox(
                "📬 Выберите компанию для просмотра питча:",
                options=company_options,
                index=company_options.index(st.session_state["selected_company"])
                if st.session_state["selected_company"] in company_options
                else 0,
                key="pitch_selector"
            )

            # Сохраняем выбор
            st.session_state["selected_company"] = selected_company

            # Находим выбранную строку
            selected_row = pitched_companies[pitched_companies["name"] == selected_company].iloc[0]
            pitch_text = selected_row["pitch_text"]
            score = selected_row["tech_score"]

            # Информация о компании
            score_icon = "❌" if pd.notna(score) and score < 50 else "⚠️" if pd.notna(score) and score < 80 else "✅"
            st.subheader(f"📬 Питч для: {selected_company}")
            maps_url = add_hl_ru(selected_row['google_maps_url']) if pd.notna(selected_row['google_maps_url']) and selected_row['google_maps_url'] else None
            maps_link = f"[📍 Карты]({maps_url})" if maps_url else "📍 Карты: —"
            st.markdown(
                f"**Score:** {score_icon} {int(score) if pd.notna(score) else '—'}  |  "
                f"**Рейтинг:** ⭐ {selected_row['google_rating']:.1f}  |  "
                f"**Сайт:** {selected_row['website'] or '—'}  |  "
                f"{maps_link}"
            )

            # --- Контакты (email, соцсети) ---
            contacts_parts = []

            # Email
            emails_raw = selected_row.get("emails")
            if pd.notna(emails_raw) and emails_raw:
                try:
                    emails_list = json.loads(emails_raw)
                    if emails_list:
                        emails_display = ", ".join(emails_list)
                        contacts_parts.append(f"📧 **Email:** {emails_display}")
                except (json.JSONDecodeError, TypeError):
                    pass

            # Соцсети
            social_raw = selected_row.get("social_links")
            if pd.notna(social_raw) and social_raw:
                try:
                    social_dict = json.loads(social_raw)
                    if social_dict:
                        social_parts = []
                        for platform, urls in social_dict.items():
                            icon = "📱"
                            if platform == "telegram":
                                icon = "✈️"
                            elif platform == "whatsapp":
                                icon = "💬"
                            for u in urls:
                                social_parts.append(f"{icon} [{platform}]({u})")
                        contacts_parts.append(" **Соцсети:** " + "  ".join(social_parts))
                except (json.JSONDecodeError, TypeError):
                    pass

            if contacts_parts:
                st.markdown("  \n".join(contacts_parts))

            st.divider()

            # Полный питч в text_area — легко выделить и скопировать
            # ВАЖНО: без key! Иначе Streamlit кэширует значение в session_state
            # и при смене компании текст питча не обновляется
            st.text_area(
                "Текст питча:",
                value=pitch_text,
                height=300,
                label_visibility="collapsed"
            )

            # --- Транспортный узел: кнопки быстрой отправки ---
            st.markdown("### 🚀 Отправить питч")
            send_links = build_send_links(pitch_text, selected_row)

            btn_col1, btn_col2, btn_col3 = st.columns(3)

            with btn_col1:
                if send_links.get("whatsapp"):
                    st.link_button("💬 WhatsApp", send_links["whatsapp"], use_container_width=True)
                else:
                    st.caption("💬 WhatsApp: нет телефона")

            with btn_col2:
                if send_links.get("telegram"):
                    st.link_button("✈️ Telegram", send_links["telegram"], use_container_width=True)
                else:
                    st.caption("✈️ Telegram: нет ссылки")

            with btn_col3:
                if send_links.get("email"):
                    st.link_button("📧 Email", send_links["email"], use_container_width=True)
                else:
                    st.caption("📧 Email: нет почты")

            # Кнопка перегенерации питча для конкретного лида
            if st.button("🔄 Перегенерировать питч", key=f"regen_{selected_row['id']}"):
                with st.spinner("🤖 Генерация нового варианта..."):
                    try:
                        from services.pitch_builder import generate_single_pitch
                        result = generate_single_pitch(int(selected_row['id']))
                        if result:
                            st.success(f"✅ Новый питч для **{result['name']}** сгенерирован!")
                            st.cache_resource.clear()
                            st.rerun()
                        else:
                            st.error("❌ Не удалось сгенерировать питч")
                    except Exception as e:
                        st.error(f"❌ Ошибка: {e}")

            # Кнопка генерации JSON сайта
            st.markdown("---")
            st.markdown("### 🌐 Генерация сайта")

            # --- Селектор выбора лида для генерации сайта ---
            all_leads_for_gen = df_filtered.copy()
            if not all_leads_for_gen.empty:
                lead_options = [
                    f"{int(row['id'])} — {row['name']}" 
                    for _, row in all_leads_for_gen.iterrows()
                ]
                
                # Находим индекс текущего выбранного лида (по питчу)
                current_lead_label = f"{int(selected_row['id'])} — {selected_row['name']}"
                default_index = lead_options.index(current_lead_label) if current_lead_label in lead_options else 0
                
                selected_lead_label = st.selectbox(
                    "🏢 Выберите лид для генерации сайта:",
                    options=lead_options,
                    index=default_index,
                    key="site_gen_lead_selector"
                )
                
                # Извлекаем ID выбранного лида
                selected_lead_id = int(selected_lead_label.split(" — ")[0])
                
                # Обновляем selected_row на основе выбора из селектора
                if selected_lead_id != int(selected_row['id']):
                    gen_row = all_leads_for_gen[all_leads_for_gen["id"] == selected_lead_id].iloc[0]
                else:
                    gen_row = selected_row
            else:
                selected_lead_id = int(selected_row['id'])
                gen_row = selected_row

            # Проверяем, есть ли уже сгенерированный JSON
            site_config_exists = pd.notna(gen_row.get('site_config_path')) and gen_row.get('site_config_path')
            if site_config_exists and Path(str(gen_row['site_config_path'])).exists():
                st.success(f"✅ JSON уже сгенерирован: `{gen_row['site_config_path']}`")
                
                # Показываем превью JSON
                try:
                    with open(str(gen_row['site_config_path']), 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                    with st.expander("📄 Просмотр JSON"):
                        json_string = json.dumps(config_data, ensure_ascii=False, indent=2)
                        st.code(json_string, language="json")
                except Exception:
                    pass
            else:
                if st.button("🌐 Сгенерировать сайт (JSON)", key=f"gen_site_{selected_lead_id}"):
                    with st.spinner("🤖 Генерация JSON конфигурации сайта..."):
                        try:
                            from services.site_config_generator import generate_site_config
                            # Загружаем данные скрейпинга если есть
                            scraped_data = load_scraped_data_for_lead(selected_lead_id)
                            result = generate_site_config(selected_lead_id, scraped_data)
                            if result:
                                st.success(f"✅ JSON сайта сгенерирован для **{result['name']}**!")
                                st.info(f"📁 Файл: `{result['file_path']}`")
                                
                                # Показываем превью
                                with st.expander("📄 Просмотр JSON"):
                                    json_string = json.dumps(result['config'], ensure_ascii=False, indent=2)
                                    st.code(json_string, language="json")
                                
                                st.cache_resource.clear()
                                st.rerun()
                            else:
                                st.error("❌ Не удалось сгенерировать JSON сайта")
                        except Exception as e:
                            st.error(f"❌ Ошибка: {e}")

            st.markdown("*💡 Выделите текст мышкой выше и скопируйте через Ctrl+C*")
        else:
            st.info("📭 У выбранных компаний ещё нет сгенерированных питчей. Запустите **Нейро-Сценариста** в боковой панели.")


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