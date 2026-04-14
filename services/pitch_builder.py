#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KURSOR Pitch Builder - Нейро-Сценарист для генерации продающих сообщений
Использует Fireworks AI API (DeepSeek V3)

ТРИ ЖЁСТКИЕ ВЕТКИ генерации:
- Ветка 1 (no_website): нет сайта вообще
- Ветка 2 (SITE_DEAD): сайт мёртв/заблокирован (НЕ упоминаем скорость загрузки!)
- Ветка 3 (audited): сайт медленный/кривой - стандартный питч
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import json
import asyncio
from typing import Optional
from dotenv import load_dotenv
from openai import OpenAI

from db.models import Lead
from db.database import get_async_session

load_dotenv()

# --- Константы Fireworks AI ---
FIREWORKS_API_KEY = os.getenv('FIREWORKS_API_KEY')
FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"
FIREWORKS_MODEL = "accounts/fireworks/models/deepseek-v3p2"


# =====================================================================
# СИСТЕМНЫЕ ПРОМПТЫ ДЛЯ ТРЁХ ВЕТОК
# =====================================================================

SYSTEM_PROMPT_NO_WEBSITE = """Ты маркетолог. Напиши холодное сообщение владельцу клиники, у которой НЕТ сайта.

ВАЖНО: Пиши ТОЛЬКО готовое сообщение для отправки. Никаких рассуждений, планов, черновиков или пояснений - сразу финальный текст сообщения!

Фреймворк сообщения:
- У них высокий рейтинг в Гугле и отличные отзывы, но НЕТ сайта
- Клиенты ищут их на картах, но им некуда перейти, чтобы посмотреть прайс и записаться онлайн
- Они уходят к клиникам с готовыми сайтами
- Мы можем за 3 дня запустить современный сайт с онлайн-записью, который сразу начнёт приносить заявки с карт

ПРАВИЛА:
1. Не используй технические термины - говори простым языком
2. Используй конкретные цифры: рейтинг, количество отзывов
3. Подчёркивай: у них отличный продукт (отзывы это подтверждают), но нет инструмента продаж (сайта)
4. ЖЁСТКИЙ ЗАПРЕТ НА ВЫДУМКИ ОТЗЫВОВ:
   - Если в данных НЕТ негативных отзывов — КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО упоминать отзывы в тексте письма
   - НИКОГДА не придумывай цитаты клиентов, жалобы на сайт или навигацию
   - Используй ТОЛЬКО те отзывы, которые реально переданы в переменной "Отзывы клиентов"
   - Если секция отзывов пустая или отсутствует — НЕ упоминай отзывы вообще
5. Если негативные отзывы ЕСТЬ и они про невозможность записаться онлайн — используй их, цитируй реальные боли
6. Предлагай решение: современный сайт с онлайн-записью за 3 дня
7. В конце предложи созвониться на 10 минут
8. Пиши уважительно, но дави на упущенную выгоду

АЛЬТЕРНАТИВНЫЙ АРГУМЕНТ (если нет отзывов для использования):
- Если негативных отзывов нет, не выдумывай их!
- Вместо этого дави на упущенную выгоду: клиенты ищут клинику на картах, видят высокий рейтинг, хотят записаться, но им некуда перейти
- Пока у них нет сайта, клиенты уходят к конкурентам с готовыми сайтами и онлайн-записью

Формат сообщения:
- Приветствие по имени компании
- Комплимент: отличный рейтинг и отзывы
- Проблема: но нет сайта, клиенты уходят к конкурентам
- Решение: сайт с онлайн-записью за 3 дня
- Призыв к действию (звонок)

Пиши сразу готовое сообщение, без вступлений и пояснений!"""


SYSTEM_PROMPT_SITE_DEAD = """Ты маркетолог-стратег. Напиши холодное сообщение владельцу клиники, чей сайт НЕДОСТУПЕН.

ВАЖНО: Пиши ТОЛЬКО готовое сообщение для отправки. Никаких рассуждений, планов, черновиков или пояснений - сразу финальный текст сообщения!

КРИТИЧЕСКИ ВАЖНО: В этом сообщении ЗАПРЕЩЕНО упоминать скорость загрузки, медленность сайта или секунды.
Сайт НЕ ПРОСТО МЕДЛЕННЫЙ - он МЁРТВ. Он заблокирован или выдаёт ошибку. Это совершенно другая проблема.

Фреймворк сообщения:
- Вас ищут на картах сотни людей каждый день
- У вас отличные отзывы, но ваш сайт сейчас недоступен (заблокирован или выдаёт ошибку)
- Каждый день без работающего сайта - это потерянные клиенты, которые не могут записаться онлайн
- Мы можем быстро восстановить его работу или собрать новый за 3 дня

ПРАВИЛА:
1. НЕ упоминай скорость загрузки, секунды, медленную загрузку - ЭТОГО НЕТ в данных
2. Говори о ПОЛНОЙ недоступности сайта, а не о его медленности
3. Используй конкретные цифры: рейтинг, количество отзывов
4. ЖЁСТКИЙ ЗАПРЕТ НА ВЫДУМКИ ОТЗЫВОВ:
   - Если в данных НЕТ негативных отзывов — КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО упоминать отзывы в тексте письма
   - НИКОГДА не придумывай цитаты клиентов, жалобы на невозможность записаться
   - Используй ТОЛЬКО те отзывы, которые реально переданы в переменной "Отзывы клиентов"
   - Если секция отзывов пустая или отсутствует — НЕ упоминай отзывы вообще
5. Если негативные отзывы ЕСТЬ и они про невозможность записаться — используй их, цитируй реальные боли
6. Предлагай решение: восстановление или новый сайт за 3 дня
7. В конце предложи созвониться на 10 минут
8. Пиши уважительно, но дави на упущенную выгоду - каждый день без сайта = потерянные клиенты

АЛЬТЕРНАТИВНЫЙ АРГУМЕНТ (если нет отзывов для использования):
- Если негативных отзывов нет, не выдумывай их!
- Вместо этого дави на то, что каждый день без работающего сайта = потерянные заявки
- Клиенты видят клинику на картах, хотят записаться, но сайт не работает — они уходят к конкурентам

Формат сообщения:
- Приветствие по имени компании
- Комплимент: отличный рейтинг и отзывы
- Проблема: сайт недоступен, клиенты не могут записаться онлайн
- Решение: восстановление или новый сайт за 3 дня
- Призыв к действию (звонок)

Пиши сразу готовое сообщение, без вступлений и пояснений!"""


SYSTEM_PROMPT_AUDITED = """Ты маркетолог-стратег. Твоя задача - написать готовое холодное сообщение владельцу клиники.

ВАЖНО: Пиши ТОЛЬКО готовое сообщение для отправки. Никаких рассуждений, планов, черновиков или пояснений - сразу финальный текст сообщения!

Переведи технические ошибки сайта в язык потери денег и упущенной выгоды.

КРИТИЧЕСКИ ВАЖНО: Используй ТОЛЬКО те проблемы, которые реально найдены в строке "Найденные проблемы" в данных компании.
- Если в "Найденные проблемы" НЕТ упоминания про HTTPS или безопасность — ЗАПРЕЩЕНО писать про отсутствие защищённого соединения!
- Если в "Найденные проблемы" НЕТ упоминания про медленную загрузку — НЕ упоминай скорость!
- НИЧЕГО не придумывай. Пиши ТОЛЬКО про то, что реально указано в данных аудита.

ПРАВИЛА:
1. Не используй технические термины (HTTPS, H1, meta и т.д.) - говори простым языком
2. Используй конкретные цифры из данных аудита (tech_score, load_time_sec)
3. Применяй фреймворк: Вы теряете X из 10 клиентов, зашедших на ваш сайт, потому что...
4. Упоминай конкурентов - пока они ждут загрузки, клиенты уходят к ним
5. Предлагай решение и короткий звонок
6. Пиши уважительно, но дави на упущенную выгоду
7. В конце предложи созвониться на 10 минут
8. ЖЁСТКИЙ ЗАПРЕТ НА ВЫДУМКИ:
   - Отзывы: если в данных НЕТ негативных отзывов — НЕ упоминай отзывы вообще
   - Проблемы сайта: упоминай ТОЛЬКО то, что реально написано в строке "Найденные проблемы"
   - НИКОГДА не придумывай проблемы которых нет в данных (например "нет HTTPS" если его нет в audit_notes)

АЛЬТЕРНАТИВНЫЙ АРГУМЕНТ (если нет отзывов для использования):
- Если негативных отзывов нет, не выдумывай их!
- Вместо этого дави на упущенную выгоду из-за технических проблем сайта
- Используй данные аудита: медленная загрузка (load_time_sec), низкий tech_score — ТОЛЬКО если они реально есть в данных
- Аргумент: "Пока ваш сайт грузится, клиенты уходят к конкурентам с более удобными сайтами"
- Подчеркни: каждый второй (третий, пятый) посетитель уходит, не дождавшись загрузки

Формат сообщения:
- Приветствие по имени компании
- Конкретная проблема с цифрами (ТОЛЬКО из реальных данных!)
- Последствия в деньгах/клиентах
- Предложение решения
- Призыв к действию (звонок)

Пиши сразу готовое сообщение, без вступлений и пояснений!"""


# =====================================================================
# ФУНКЦИИ ФОРМИРОВАНИЯ USER-ПРОМПТОВ
# =====================================================================

def _build_reviews_section(lead: Lead, emphasis: str = "general") -> str:
    """Общая функция для формирования секции отзывов"""
    if not lead.raw_reviews:
        return "\n- Негативные отзывы: НЕТ (отзывов не обнаружено)"
    try:
        negative_reviews = json.loads(lead.raw_reviews)
        if not negative_reviews:
            return "\n- Негативные отзывы: НЕТ (отзывов не обнаружено)"
        reviews_lines = []
        for rev in negative_reviews[:5]:
            rating = rev.get("rating", "?")
            text = rev.get("text", "")[:200]
            reviews_lines.append(f'  *{rating}: "{text}"')
        header = "\n- Отзывы клиентов (используй ТОЛЬКО эти реальные отзывы, НЕ выдумывай свои!):\n"
        return header + chr(10).join(reviews_lines)
    except (json.JSONDecodeError, TypeError):
        return "\n- Негативные отзывы: НЕТ (ошибка чтения данных)"


def build_user_prompt_no_website(lead: Lead) -> str:
    """Ветка 1: НЕТ САЙТА ВООБЩЕ"""
    name = lead.name or "Ваша клиника"
    google_rating = lead.google_rating or "?"
    reviews_count = lead.reviews_count or 0
    address = lead.address or ""
    reviews_section = _build_reviews_section(lead, emphasis="online_booking")
    return f"""Данные компании:
- Название: {name}
- Рейтинг в Google: {google_rating} звёзд
- Количество отзывов: {reviews_count}
- Адрес: {address}
- САЙТА НЕТ - это главная проблема{reviews_section}

ВАЖНО: Если в строке "Негативные отзывы" написано "НЕТ" — НЕ упоминай отзывы в письме, используй альтернативный аргумент про упущенную выгоду.
Напиши холодное сообщение владельцу этой клиники о необходимости создать сайт."""


def build_user_prompt_site_dead(lead: Lead) -> str:
    """Ветка 2: САЙТ МЁРТВ / ЗАБЛОКИРОВАН"""
    name = lead.name or "Ваша клиника"
    website = lead.website or "сайт"
    google_rating = lead.google_rating or "?"
    reviews_count = lead.reviews_count or 0
    audit_notes = lead.audit_notes or ""
    reviews_section = _build_reviews_section(lead, emphasis="online_booking")
    return f"""Данные компании:
- Название: {name}
- Сайт: {website} - СЕЙЧАС НЕДОСТУПЕН (заблокирован или выдаёт ошибку)
- Рейтинг в Google: {google_rating} звёзд
- Количество отзывов: {reviews_count}
- Технический аудит: {audit_notes}{reviews_section}

ВАЖНО: Сайт НЕ медленный - он ПОЛНОСТЬЮ недоступен. Не упоминай скорость загрузки!
ВАЖНО: Если в строке "Негативные отзывы" написано "НЕТ" — НЕ упоминай отзывы в письме, используй альтернативный аргумент про потерянные заявки.
Напиши холодное сообщение владельцу этой клиники о том, что их сайт недоступен и они теряют клиентов."""


def build_user_prompt_audited(lead: Lead) -> str:
    """Ветка 3: САЙТ МЕДЛЕННЫЙ / КРИВОЙ"""
    name = lead.name or "Ваша клиника"
    website = lead.website or "сайт"
    audit_notes = lead.audit_notes or "проблемы не выявлены"
    tech_score = lead.tech_score or 0
    load_time = lead.load_time_sec or 0
    lost_clients = round((100 - tech_score) / 10, 1) if tech_score < 100 else 0
    reviews_section = _build_reviews_section(lead, emphasis="general")
    return f"""Данные компании:
- Название: {name}
- Сайт: {website}
- Технический балл: {tech_score}/100
- Время загрузки: {load_time:.1f} сек
- Найденные проблемы: {audit_notes}
- Примерная потеря клиентов: {lost_clients} из 10 зашедших на сайт{reviews_section}

ВАЖНО: Если в строке "Негативные отзывы" написано "НЕТ" — НЕ упоминай отзывы в письме, используй альтернативный аргумент про упущенную выгоду из-за технических проблем.
Напиши холодное сообщение владельцу этой клиники."""


# =====================================================================
# ОПРЕДЕЛЕНИЕ ВЕТКИ
# =====================================================================

def determine_branch(lead: Lead) -> str:
    """
    Определяет, какую ветку питча использовать.

    Ветка 1 (no_website): status == 'no_website'
    Ветка 2 (site_dead): '[SITE_DEAD]' в audit_notes или load_time_sec in (None, 0)
    Ветка 3 (audited): всё остальное (медленный/кривой сайт)
    """
    if lead.status == 'no_website':
        return 'no_website'

    audit_notes = lead.audit_notes or ""
    if '[SITE_DEAD]' in audit_notes or lead.load_time_sec in (None, 0):
        return 'site_dead'

    return 'audited'


# =====================================================================
# ВЫЗОВ LLM
# =====================================================================

def safe_print(text: str) -> None:
    """Безопасный print с защитой от charmap ошибок на Windows."""
    try:
        print(text)
    except (UnicodeEncodeError, UnicodeError):
        # Фоллбэк: заменяем проблемные символы
        print(text.encode('cp1251', errors='replace').decode('cp1251'))


def get_fireworks_client() -> Optional[OpenAI]:
    """Создаёт клиент Fireworks AI"""
    if not FIREWORKS_API_KEY:
        safe_print("[X] FIREWORKS_API_KEY не найден в .env")
        return None
    return OpenAI(base_url=FIREWORKS_BASE_URL, api_key=FIREWORKS_API_KEY)


def call_llm(user_prompt: str, system_prompt: str, lead_name: str = "") -> Optional[str]:
    """Вызывает Fireworks AI API для генерации питча"""
    client = get_fireworks_client()
    if not client:
        return None
    try:
        safe_print(f"[GEN] Генерация через Fireworks AI ({FIREWORKS_MODEL})...")
        response = client.chat.completions.create(
            model=FIREWORKS_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=500
        )
        content = response.choices[0].message.content
        return content.strip()
    except Exception as e:
        safe_print(f"[ERR] Ошибка Fireworks AI: {e}")
        return None


# =====================================================================
# ГЛАВНАЯ ФУНКЦИЯ ГЕНЕРАЦИИ
# =====================================================================

async def generate_pitches(batch_size: int = 5) -> dict:
    """
    Генерирует питчи по ТРЁМ ЖЁСТКИМ ВЕТКАМ:
    - Ветка 1 (no_website): нет сайта вообще
    - Ветка 2 (site_dead): сайт мёртв/заблокирован
    - Ветка 3 (audited): сайт медленный/кривой
    """
    from sqlalchemy import select, update

    stats = {
        "total": 0,
        "generated": 0,
        "skipped": 0,
        "errors": 0,
        "results": []
    }

    async with get_async_session() as session:
        result = await session.execute(
            select(Lead)
            .where(Lead.status.in_(["audited", "no_website"]))
            .where(Lead.pitch_text.is_(None))
        )
        leads = result.scalars().all()

        stats["total"] = len(leads)

        if not leads:
            safe_print("[INFO] Нет лидов для генерации питчей")
            return stats

        branch_counts = {"no_website": 0, "site_dead": 0, "audited": 0}
        for lead in leads:
            branch = determine_branch(lead)
            branch_counts[branch] += 1

        safe_print(f"[INFO] Найдено лидов для генерации питчей: {len(leads)}")
        safe_print(f"   Ветка 1 (нет сайта): {branch_counts['no_website']}")
        safe_print(f"   Ветка 2 (сайт мёртв): {branch_counts['site_dead']}")
        safe_print(f"   Ветка 3 (медленный/кривой): {branch_counts['audited']}")

        for lead in leads:
            branch = determine_branch(lead)

            if branch == 'no_website':
                safe_print(f"\n[BRANCH 1: НЕТ САЙТА] Генерация для: {lead.name}")
                user_prompt = build_user_prompt_no_website(lead)
                system_prompt = SYSTEM_PROMPT_NO_WEBSITE
                branch_label = "no_website"

            elif branch == 'site_dead':
                safe_print(f"\n[BRANCH 2: САЙТ МЁРТВ] Генерация для: {lead.name} (сайт: {lead.website})")
                user_prompt = build_user_prompt_site_dead(lead)
                system_prompt = SYSTEM_PROMPT_SITE_DEAD
                branch_label = "site_dead"

            else:
                safe_print(f"\n[BRANCH 3: МЕДЛЕННЫЙ] Генерация для: {lead.name} (score={lead.tech_score})")
                user_prompt = build_user_prompt_audited(lead)
                system_prompt = SYSTEM_PROMPT_AUDITED
                branch_label = "audited"

            pitch = call_llm(user_prompt, system_prompt, lead_name=lead.name)

            if pitch:
                await session.execute(
                    update(Lead)
                    .where(Lead.id == lead.id)
                    .values(pitch_text=pitch, status="pitched")
                )
                stats["generated"] += 1
                stats["results"].append({
                    "lead_id": lead.id, "name": lead.name,
                    "type": branch_label, "pitch": pitch
                })
                safe_print(f"[OK] Питч [{branch_label}] сгенерирован:\n{pitch[:300]}...")
            else:
                stats["errors"] += 1
                safe_print(f"[ERR] Ошибка генерации для {lead.name}")

        await session.commit()

    return stats


async def generate_single_pitch_async(lead_id: int) -> Optional[dict]:
    """
    Генерирует питч для ОДНОГО конкретного лида по ID.
    Игнорирует текущий статус (позволяет перегенерировать уже готовый питч).
    """
    from sqlalchemy import select, update

    async with get_async_session() as session:
        result = await session.execute(
            select(Lead).where(Lead.id == lead_id)
        )
        lead = result.scalar_one_or_none()

        if not lead:
            safe_print(f"[ERR] Лид с ID={lead_id} не найден")
            return None

        branch = determine_branch(lead)

        if branch == 'no_website':
            safe_print(f"[BRANCH 1: НЕТ САЙТА] Перегенерация для: {lead.name}")
            user_prompt = build_user_prompt_no_website(lead)
            system_prompt = SYSTEM_PROMPT_NO_WEBSITE
        elif branch == 'site_dead':
            safe_print(f"[BRANCH 2: САЙТ МЁРТВ] Перегенерация для: {lead.name}")
            user_prompt = build_user_prompt_site_dead(lead)
            system_prompt = SYSTEM_PROMPT_SITE_DEAD
        else:
            safe_print(f"[BRANCH 3: МЕДЛЕННЫЙ] Перегенерация для: {lead.name}")
            user_prompt = build_user_prompt_audited(lead)
            system_prompt = SYSTEM_PROMPT_AUDITED

        pitch = call_llm(user_prompt, system_prompt, lead_name=lead.name)

        if pitch:
            await session.execute(
                update(Lead)
                .where(Lead.id == lead.id)
                .values(pitch_text=pitch, status="pitched")
            )
            await session.commit()
            safe_print(f"[OK] Питч [{branch}] перегенерирован для {lead.name}")
            return {
                "lead_id": lead.id,
                "name": lead.name,
                "type": branch,
                "pitch": pitch
            }
        else:
            safe_print(f"[ERR] Ошибка генерации питча для {lead.name}")
            return None


def generate_single_pitch(lead_id: int) -> Optional[dict]:
    """Синхронная обертка для генерации питча одного лида"""
    return asyncio.run(generate_single_pitch_async(lead_id))


def generate_pitches_sync() -> dict:
    """Синхронная обертка для вызова из дашборда"""
    return asyncio.run(generate_pitches())


# --- CLI запуск ---
if __name__ == "__main__":
    safe_print("[START] KURSOR Pitch Builder - Нейро-Сценарист (Fireworks AI)")
    safe_print("=" * 55)
    safe_print(f"  API: {FIREWORKS_BASE_URL}")
    safe_print(f"  Модель: {FIREWORKS_MODEL}")
    key_status = "[OK] установлен" if FIREWORKS_API_KEY else "[X] НЕ НАЙДЕН"
    safe_print(f"  Ключ: {key_status}")
    safe_print("=" * 55)

    result = generate_pitches_sync()

    safe_print(f"\n[RESULTS] Результаты:")
    safe_print(f"   Всего лидов: {result['total']}")
    safe_print(f"   Питчей сгенерировано: {result['generated']}")
    safe_print(f"   Ошибок: {result['errors']}")

    if result['results']:
        safe_print(f"\n[PITCHES] Сгенерированные питчи:")
        for r in result['results'][:5]:
            branch_labels = {
                "no_website": "ВЕТКА 1 (нет сайта)",
                "site_dead": "ВЕТКА 2 (сайт мёртв)",
                "audited": "ВЕТКА 3 (медленный)",
            }
            type_label = branch_labels.get(r['type'], r['type'])
            safe_print(f"\n{'='*60}")
            safe_print(f"  {r['name']} (ID={r['lead_id']}) [{type_label}]")
            safe_print(f"{'='*60}")
            safe_print(r['pitch'])