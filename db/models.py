#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLAlchemy models for KURSOR Radar
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Базовый класс для моделей"""
    pass


class Lead(Base):
    """
    Модель лида (потенциального клиента)

    Атрибуты:
        id: Уникальный идентификатор
        name: Название компании
        google_rating: Рейтинг в Google (0-5)
        reviews_count: Количество отзывов
        address: Адрес компании
        phone: Телефон (может быть None)
        website: URL сайта (NULL для компаний без сайта, SQLite допускает много NULL в UNIQUE)
        status: Статус лида (new, audited, no_website, pitched, contacted, converted, rejected, error)
        tech_score: Технический балл сайта (0-100)
        load_time_sec: Время загрузки сайта в секундах
        audit_notes: Заметки аудита (найденные проблемы)
        pitch_text: Сгенерированный питч
        raw_reviews: Негативные отзывы (JSON)
        created_at: Дата создания записи
        updated_at: Дата последнего обновления
    """
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    google_rating = Column(Float, nullable=True)
    reviews_count = Column(Integer, nullable=True, default=0)
    address = Column(String(500), nullable=True)
    phone = Column(String(50), nullable=True)
    website = Column(String(500), nullable=True, unique=True)  # NULL для компаний без сайта
    google_maps_url = Column(String(500), nullable=True)  # Ссылка на Google Maps
    emails = Column(Text, nullable=True)  # Найденные email-адреса (JSON-массив)
    social_links = Column(Text, nullable=True)  # Ссылки на соцсети/мессенджеры (JSON-массив)
    status = Column(String(50), nullable=False, default="new")
    tech_score = Column(Integer, nullable=True)
    load_time_sec = Column(Float, nullable=True)
    audit_notes = Column(Text, nullable=True)
    pitch_text = Column(Text, nullable=True)
    raw_reviews = Column(Text, nullable=True)
    site_config_path = Column(Text, nullable=True)  # Путь к JSON конфигу сайта
    category = Column(String(100), nullable=True, default="other")  # Категория/ниша лида
    qualification_status = Column(String(50), nullable=True, default="pending")  # Статус квалификации для фото-обогащения
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Связи
    audit_logs = relationship("AuditLog", back_populates="lead", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Lead(id={self.id}, name='{self.name}', website='{self.website}', status='{self.status}')>"

    def to_dict(self) -> dict:
        """Преобразует модель в словарь"""
        return {
            "id": self.id,
            "name": self.name,
            "google_rating": self.google_rating,
            "reviews_count": self.reviews_count,
            "address": self.address,
            "phone": self.phone,
            "website": self.website,
            "google_maps_url": self.google_maps_url,
            "emails": self.emails,
            "social_links": self.social_links,
            "status": self.status,
            "tech_score": self.tech_score,
            "load_time_sec": self.load_time_sec,
            "audit_notes": self.audit_notes,
            "pitch_text": self.pitch_text,
            "raw_reviews": self.raw_reviews,
            "site_config_path": self.site_config_path,
            "category": self.category,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AuditLog(Base):
    """
    Модель лога операций над лидами и системой.

    Атрибуты:
        id: Уникальный идентификатор
        lead_id: ID лида (NULL для системных операций)
        action: Тип действия (created, status_changed, parsed, pitched, contacted, error)
        details: JSON с дополнительным контекстом
        created_at: Когда произошло действие
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(50), nullable=False, index=True)
    details = Column(Text, nullable=True)  # JSON
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    # Связи
    lead = relationship("Lead", back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, lead_id={self.lead_id}, action='{self.action}')>"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "action": self.action,
            "details": self.details,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
