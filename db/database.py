#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Database configuration and session management for KURSOR Radar
Uses SQLAlchemy with aiosqlite for async SQLite operations
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from contextlib import asynccontextmanager

from .models import Base, Lead, AuditLog
from .schemas import LogCreated, LogStatusChanged


# Путь к файлу базы данных
DB_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DB_DIR / "leads.db"
DB_URL = f"sqlite+aiosqlite:///{DB_PATH}"


# Создаем асинхронный движок
engine = create_async_engine(
    DB_URL,
    echo=False,
    future=True,
)

# Фабрика сессий
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Инициализация базы данных - создание всех таблиц"""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print(f"[DB] База данных инициализирована: {DB_PATH}")


@asynccontextmanager
async def get_session():
    """Контекстный менеджер для работы с сессией БД"""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_async_session():
    """Алиас для get_session (для совместимости)"""
    async with get_session() as session:
        yield session


async def _touch_lead(session: AsyncSession, lead: Lead) -> None:
    """Обновить updated_at у лида."""
    lead.updated_at = datetime.now(timezone.utc)


async def _log_action(
    session: AsyncSession,
    action: str,
    lead_id: Optional[int] = None,
    details: Optional[dict] = None,
) -> AuditLog:
    """Создать запись в audit_logs. Валидирует details через Pydantic перед записью."""
    if action == "created":
        details = LogCreated.model_validate(details).model_dump()
    elif action == "status_changed":
        details = LogStatusChanged.model_validate(details).model_dump(by_alias=True)
    elif details is not None and not isinstance(details, dict):
        raise ValueError(f"details must be dict or None, got {type(details).__name__}")

    log = AuditLog(
        lead_id=lead_id,
        action=action,
        details=json.dumps(details, ensure_ascii=False) if details else None,
        created_at=datetime.now(timezone.utc),
    )
    session.add(log)
    return log


async def upsert_lead(
    name: str,
    website: str = None,
    google_rating: float = None,
    reviews_count: int = None,
    address: str = None,
    phone: str = None,
    google_maps_url: str = None,
    status: str = "new",
    raw_reviews: str = None,
    category: str = "other",
) -> Lead:
    """
    Upsert операция для лида

    Если сайт уже существует в БД - обновляет запись
    Если нет сайта - ищет по name+address (для компаний без сайта)
    Если ничего не найдено - создает новую запись
    """
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        existing_lead = None

        # Если есть website — ищем по website
        if website:
            result = await session.execute(
                select(Lead).where(Lead.website == website)
            )
            existing_lead = result.scalar_one_or_none()

        # Если нет website — ищем по name+address (дедупликация для без-сайтовых)
        if not existing_lead and not website:
            query = select(Lead).where(Lead.name == name)
            if address:
                query = query.where(Lead.address == address)
            result = await session.execute(query)
            existing_lead = result.scalar_one_or_none()

        if existing_lead:
            # Обновляем существующую запись
            existing_lead.name = name
            if google_rating is not None:
                existing_lead.google_rating = google_rating
            if reviews_count is not None:
                existing_lead.reviews_count = reviews_count
            if address is not None:
                existing_lead.address = address
            if phone is not None:
                existing_lead.phone = phone
            if website is not None:
                existing_lead.website = website
            if raw_reviews is not None:
                existing_lead.raw_reviews = raw_reviews
            if google_maps_url is not None:
                existing_lead.google_maps_url = google_maps_url
            if category is not None and category != "other":
                existing_lead.category = category

            existing_lead.updated_at = now

            await session.commit()
            await session.refresh(existing_lead)
            print(f"[DB] Обновлен лид: {name} ({website or 'нет сайта'})")
            return existing_lead
        else:
            # Создаем новую запись
            new_lead = Lead(
                name=name,
                website=website,
                google_rating=google_rating,
                reviews_count=reviews_count,
                address=address,
                phone=phone,
                google_maps_url=google_maps_url,
                status=status,
                raw_reviews=raw_reviews,
                category=category,
                created_at=now,
                updated_at=now,
            )
            session.add(new_lead)
            await session.flush()  # получаем ID

            # Логируем создание
            await _log_action(session, "created", lead_id=new_lead.id, details={
                "name": name,
                "website": website,
                "status": status,
                "category": category,
            })

            await session.commit()
            await session.refresh(new_lead)
            print(f"[DB] Создан новый лид: {name} ({website or 'нет сайта'})")
            return new_lead


async def get_all_leads(
    status: str = None,
    page: int = 1,
    page_size: int = 50,
    sort_by: str = "id",
    sort_order: str = "desc",
) -> tuple[list[Lead], int]:
    """Получить лиды с пагинацией.

    Args:
        status: Фильтр по статусу (None = все)
        page: Номер страницы (1-based)
        page_size: Размер страницы
        sort_by: Поле сортировки (id, name, status, created_at, updated_at, google_rating, tech_score)
        sort_order: Направление (asc, desc)

    Returns:
        (список лидов, общее количество)
    """
    async with async_session() as session:
        # Считаем общее количество
        count_query = select(func.count(Lead.id))
        if status:
            count_query = count_query.where(Lead.status == status)
        count_result = await session.execute(count_query)
        total = count_result.scalar() or 0

        # Строим запрос с сортировкой
        query = select(Lead)
        if status:
            query = query.where(Lead.status == status)

        # Валидация sort_by
        allowed_sort = {"id", "name", "status", "created_at", "updated_at", "google_rating", "tech_score", "reviews_count"}
        if sort_by not in allowed_sort:
            sort_by = "id"

        sort_col = getattr(Lead, sort_by)
        if sort_order == "asc":
            query = query.order_by(sort_col.asc())
        else:
            query = query.order_by(sort_col.desc())

        # Пагинация
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)

        result = await session.execute(query)
        leads = result.scalars().all()

        return list(leads), total


async def get_lead_by_website(website: str) -> Lead | None:
    """Найти лид по URL сайта"""
    async with async_session() as session:
        result = await session.execute(
            select(Lead).where(Lead.website == website)
        )
        return result.scalar_one_or_none()


async def get_lead_by_id(lead_id: int) -> Lead | None:
    """Найти лид по ID"""
    async with async_session() as session:
        result = await session.execute(
            select(Lead).where(Lead.id == lead_id)
        )
        return result.scalar_one_or_none()


async def update_lead_status(lead_id: int, new_status: str) -> Lead | None:
    """Обновить статус лида с логированием"""
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        result = await session.execute(
            select(Lead).where(Lead.id == lead_id)
        )
        lead = result.scalar_one_or_none()
        if lead:
            old_status = lead.status
            lead.status = new_status
            lead.updated_at = now

            await _log_action(session, "status_changed", lead_id=lead.id, details={
                "from": old_status,
                "to": new_status,
            })

            await session.commit()
            await session.refresh(lead)
            return lead
        return None


async def get_audit_logs(
    lead_id: Optional[int] = None,
    action: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[AuditLog], int]:
    """Получить логи операций с пагинацией.

    Args:
        lead_id: Фильтр по ID лида (None = все)
        action: Фильтр по типу действия (None = все)
        page: Номер страницы (1-based)
        page_size: Размер страницы

    Returns:
        (список логов, общее количество)
    """
    async with async_session() as session:
        count_query = select(func.count(AuditLog.id))
        query = select(AuditLog)

        if lead_id is not None:
            count_query = count_query.where(AuditLog.lead_id == lead_id)
            query = query.where(AuditLog.lead_id == lead_id)
        if action is not None:
            count_query = count_query.where(AuditLog.action == action)
            query = query.where(AuditLog.action == action)

        count_result = await session.execute(count_query)
        total = count_result.scalar() or 0

        query = query.order_by(AuditLog.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await session.execute(query)
        logs = result.scalars().all()

        return list(logs), total


async def log_action(
    lead_id: Optional[int] = None,
    action: str = "system",
    details: Optional[dict] = None,
) -> AuditLog:
    """Создать лог операции (вне контекста другой операции)."""
    async with async_session() as session:
        log = await _log_action(session, action, lead_id=lead_id, details=details)
        await session.commit()
        await session.refresh(log)
        return log


async def get_leads_count_by_status() -> dict[str, int]:
    """Получить количество лидов по каждому статусу."""
    async with async_session() as session:
        result = await session.execute(
            select(Lead.status, func.count(Lead.id))
            .group_by(Lead.status)
        )
        counts = {"total": 0}
        for row in result.all():
            counts[row[0]] = row[1]
            counts["total"] += row[1]
        return counts
