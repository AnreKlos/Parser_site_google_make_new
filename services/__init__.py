#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
services/ — бизнес-логика над данными из БД.

Слой отвечает за: загрузку лидов из БД → анализ/обогащение/генерацию
                  → запись результатов обратно в БД.

Правило: модули в этом пакете работают с lead_id и моделью Lead.
Чистый парсинг URL без БД делегируется в core/.

См. README.md, раздел "🏗 Архитектура".
"""

from .google_radar import search_and_save, show_saved_leads
from .pitch_builder import generate_pitches, generate_single_pitch, generate_pitches_sync
from .site_config_generator import generate_site_config, generate_site_configs_sync
from .xray_auditor import run_xray_audit, run_xray_audit_sync

__all__ = [
    "search_and_save", "show_saved_leads",
    "generate_pitches", "generate_single_pitch", "generate_pitches_sync",
    "generate_site_config", "generate_site_configs_sync",
    "run_xray_audit", "run_xray_audit_sync",
]
