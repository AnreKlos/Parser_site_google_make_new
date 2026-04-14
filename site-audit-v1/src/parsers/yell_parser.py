from __future__ import annotations

import re
import time
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from requests.exceptions import RequestException

from src.models import BusinessRecord
from config.settings import settings


class YellParser:
    """
    Парсер yell.ru (справочник организаций) с использованием requests + BeautifulSoup.
    """

    # Словарь для преобразования запросов в slugs
    QUERY_SLUGS = {
        "салоны красоты": "salony-krasoty",
        "салон красоты": "salony-krasoty",
        "стоматология": "stomatologii",
        "кафе": "cafe",
        "рестораны": "restorany",
    }

    def __init__(self, pause_sec: Optional[float] = None):
        self.pause_sec = pause_sec if pause_sec is not None else settings.MIN_PAUSE_SEC
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": settings.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        })

    def slugify_city(self, city: str) -> str:
        """
        Преобразует название города в slug для URL yell.ru.
        Поддерживаемые города с транслитерацией:
        "москва"→"moscow", "санкт-петербург"/"спб"→"spb", "казань"→"kazan",
        "брянск"→"bryansk", "новосибирск"→"novosibirsk", "екатеринбург"→"ekaterinburg",
        "нижний новгород"→"nizhniy-novgorod", "ростов-на-дону"→"rostov-na-donu",
        "уфа"→"ufa", "красноярск"→"krasnoyarsk", "самара"→"samara".
        Остальные: транслитерация кириллицы в латиницу.
        """
        city = city.strip().lower()
        mapping = {
            "москва": "moscow",
            "санкт-петербург": "spb",
            "спб": "spb",
            "казань": "kazan",
            "брянск": "bryansk",
            "новосибирск": "novosibirsk",
            "екатеринбург": "ekaterinburg",
            "нижний новгород": "nizhniy-novgorod",
            "ростов-на-дону": "rostov-na-donu",
            "уфа": "ufa",
            "красноярск": "krasnoyarsk",
            "самара": "samara",
        }
        if city in mapping:
            return mapping[city]
        # Простая транслитерация для остальных городов
        translit_map = {
            'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
            'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
            'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
            'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
            'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya', ' ': '-'
        }
        result = ''.join(translit_map.get(c, c) for c in city)
        return result

    def slugify_query(self, query: str) -> str:
        """
        Преобразует поисковый запрос в slug для URL yell.ru.
        Если запрос есть в словаре QUERY_SLUGS, возвращает соответствующий slug.
        Иначе возвращает пустую строку (неподдерживаемый запрос).
        """
        normalized = query.strip().lower()
        return self.QUERY_SLUGS.get(normalized, "")

    def search(self, query: str, city: str, limit: int = 20) -> List[BusinessRecord]:
        """
        Поиск организаций на yell.ru через top-страницы.

        Args:
            query: Поисковый запрос (например, "стоматология")
            city: Город (например, "Казань")
            limit: Максимальное количество результатов

        Returns:
            Список BusinessRecord, может быть пустым
        """
        city_slug = self.slugify_city(city)
        query_slug = self.slugify_query(query)

        if not query_slug:
            print(f"[Yell] Категория '{query}' не поддерживается. Поддерживаются: {list(self.QUERY_SLUGS.keys())}")
            return []

        results: List[BusinessRecord] = []
        seen_urls = set()

        # Пагинация: начинаем с page=1
        page = 1
        while len(results) < limit:
            # Формируем URL с пагинацией
            if page == 1:
                search_url = f"https://www.yell.ru/{city_slug}/top/{query_slug}/"
            else:
                search_url = f"https://www.yell.ru/{city_slug}/top/{query_slug}/?page={page}"

            try:
                print(f"[Yell] Запрос: {search_url}")
                response = self.session.get(search_url, timeout=30)

                if response.status_code in (403, 429):
                    print(f"[Yell] Доступ запрещён (403) или слишком много запросов (429)")
                    break

                if response.status_code == 404:
                    print(f"[Yell] Страница не найдена (404): {search_url}")
                    break

                response.raise_for_status()

            except RequestException as e:
                print(f"[Yell] Ошибка HTTP запроса: {e}")
                break
            except Exception as e:
                print(f"[Yell] Неожиданная ошибка при запросе: {e}")
                break

            soup = BeautifulSoup(response.text, "html.parser")

            # Находим ссылки на карточки компаний
            # Ищем ссылки с классом companies__item-title-text, которые ведут на страницы компаний
            card_links = soup.select('a.companies__item-title-text')
            print(f"[Yell] Страница {page}: найдено ссылок на компании: {len(card_links)}")

            if not card_links:
                # Нет больше результатов
                break

            for link in card_links:
                if len(results) >= limit:
                    break

                href = link.get("href")
                if not href:
                    continue

                # Фильтруем только ссылки на компании (содержащие /com/)
                if '/com/' not in href:
                    continue

                # Полный URL карточки
                maps_url = href if href.startswith("http") else f"https://www.yell.ru{href}"
                
                if maps_url in seen_urls:
                    continue
                seen_urls.add(maps_url)

                # Название из текста ссылки
                business_name = link.get_text(strip=True)
                if not business_name:
                    continue

                # Пока только минимальные данные
                record = BusinessRecord(
                    business_name=business_name,
                    city=city,
                    website=None,
                    address=None,
                    phone=None,
                    maps_url=maps_url,
                    source="yell",
                )
                results.append(record)
                print(f"[Yell] OK: {business_name}")

                if self.pause_sec > 0:
                    time.sleep(self.pause_sec)

            page += 1

        print(f"[Yell] Всего собрано: {len(results)}")
        return results