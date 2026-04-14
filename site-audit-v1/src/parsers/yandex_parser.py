from __future__ import annotations

import time
from typing import List, Optional

import requests
from requests.exceptions import RequestException

from src.models import BusinessRecord
from config.settings import settings


class YandexParser:
    BASE_URL = "https://search-maps.yandex.ru/v1/"

    def __init__(self, pause_sec: Optional[float] = None):
        self.pause_sec = pause_sec if pause_sec is not None else settings.MIN_PAUSE_SEC
        self.api_key = settings.YANDEX_MAPS_API_KEY if hasattr(settings, 'YANDEX_MAPS_API_KEY') else None

    def search(self, query: str, city: str, limit: int = 20) -> List[BusinessRecord]:
        """
        Поиск организаций через Яндекс Карты API.

        Args:
            query: Поисковый запрос (например, "стоматология")
            city: Город (например, "Казань")
            limit: Максимальное количество результатов

        Returns:
            Список BusinessRecord, может быть пустым
        """
        if not self.api_key:
            raise ValueError("YANDEX_MAPS_API_KEY не настроен в .env или settings")

        # Формируем текст запроса: "стоматология Казань"
        search_text = f"{query.strip()} {city.strip()}".strip()

        params = {
            "apikey": self.api_key,
            "text": search_text,
            "type": "biz",
            "lang": "ru_RU",
            "results": limit,
        }

        try:
            print(f"[Yandex] Запрос: {search_text}")
            response = requests.get(self.BASE_URL, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            features = data.get("features", [])

            print(f"[Yandex] Найдено организаций: {len(features)}")

            results: List[BusinessRecord] = []
            for feature in features:
                try:
                    record = self._parse_feature(feature, city)
                    if record:
                        results.append(record)
                        print(f"[Yandex] OK: {record.business_name}")
                except Exception as e:
                    print(f"[Yandex] Ошибка парсинга записи: {e}")
                    continue

            return results

        except RequestException as e:
            print(f"[Yandex] Ошибка HTTP запроса: {e}")
            if hasattr(e, 'response') and e.response is not None:
                response = e.response
                print(f"[Yandex] URL запроса: {response.url}")
                print(f"[Yandex] Статус: {response.status_code}")
                body_preview = response.text[:500] if response.text else "(пусто)"
                print(f"[Yandex] Тело: {body_preview}")
                
                # Явная обработка 403 + "Limit is exceeded"
                if response.status_code == 403 and "Limit is exceeded" in response.text:
                    print("[Yandex] Ключ API синтаксически верен, но квота/доступ исчерпан или недоступен")
            return []
        except Exception as e:
            print(f"[Yandex] Неожиданная ошибка: {e}")
            return []

    def _parse_feature(self, feature: dict, city: str) -> Optional[BusinessRecord]:
        """
        Парсит одну запись из API ответа.

        Ожидаемая структура:
        {
          "properties": {
            "name": "Название",
            "CompanyMetaData": {
              "address": "Адрес",
              "phones": [{"formatted": "+7..."}],
              "url": "https://yandex.ru/maps/org/...",
              "categories": [{"name": "Категория"}],
              "website": "http://..."
            }
          }
        }
        """
        properties = feature.get("properties", {})
        company_meta = properties.get("CompanyMetaData", {})

        # Название
        business_name = properties.get("name")
        if not business_name:
            return None

        # Категория (первая из списка)
        categories = company_meta.get("categories", [])
        category = categories[0].get("name") if categories else None

        # Адрес
        address = company_meta.get("address")

        # Телефон (первый из списка)
        phones = company_meta.get("phones", [])
        phone = phones[0].get("formatted") if phones else None

        # Сайт
        website = company_meta.get("website")

        # URL карточки на Яндекс Картах
        maps_url = company_meta.get("url")

        return BusinessRecord(
            business_name=business_name,
            category=category,
            city=city,
            address=address,
            phone=phone,
            website=website,
            maps_url=maps_url,
            source="yandex_maps",
        )