#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Database configuration and session management for KURSOR Radar
Uses SQLAlchemy with aiosqlite for async SQLite operations
"""

import os
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select
from contextlib import asynccontextmanager

from .models import Base, Lead


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
) -> Lead:
    """
    Upsert операция для лида
    
    Если сайт уже существует в БД - обновляет запись
    Если нет сайта - ищет по name+address (для компаний без сайта)
    Если ничего не найдено - создает новую запись
    """
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
            # Статус не меняем - он управляется вручную
            
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
            )
            session.add(new_lead)
            await session.commit()
            await session.refresh(new_lead)
            print(f"[DB] Создан новый лид: {name} ({website or 'нет сайта'})")
            return new_lead


async def get_all_leads(status: str = None) -> list[Lead]:
    """Получить все лиды из базы"""
    async with async_session() as session:
        if status:
            result = await session.execute(
                select(Lead).where(Lead.status == status)
            )
        else:
            result = await session.execute(select(Lead))
        return result.scalars().all()


async def get_lead_by_website(website: str) -> Lead | None:
    """Найти лид по URL сайта"""
    async with async_session() as session:
        result = await session.execute(
            select(Lead).where(Lead.website == website)
        )
        return result.scalar_one_or_none()


async def update_lead_status(lead_id: int, new_status: str) -> Lead | None:
    """Обновить статус лида"""
    async with async_session() as session:
        result = await session.execute(
            select(Lead).where(Lead.id == lead_id)
        )
        lead = result.scalar_one_or_none()
        if lead:
            lead.status = new_status
            await session.commit()
            await session.refresh(lead)
            return lead
        return None