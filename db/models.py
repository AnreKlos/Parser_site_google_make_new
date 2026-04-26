#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLAlchemy models for KURSOR Radar
"""

from sqlalchemy import Column, Integer, String, Float, Text
from sqlalchemy.orm import DeclarativeBase


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
        }
