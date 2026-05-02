"""
enrichment/ — обогащение данных лидов без использования LLM.

Слой отвечает за: REST API запросы → скачивание медиа → обогащение БД.
Не содержит LLM-взаимодействие.
"""

from .yandex import enrich_lead
from .photos import fetch_photos

__all__ = ["enrich_lead", "fetch_photos"]
