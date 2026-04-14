import json
import time
import random
from typing import List, Optional
import httpx

from src.models import BusinessRecord
from config.settings import settings


class TwoGisCollector:
    def __init__(self):
        # Используем внутренний API веб-версии 2GIS
        self.api_url = "https://catalog.api.2gis.com/3.0/items"
        # Публичный ключ для веба (он у них захардкожен на сайте)
        self.key = "rurbbn3446"

    def collect(self, query: str, city: str, limit: int = 20) -> List[BusinessRecord]:
        """
        Собирает компании из 2GIS по запросу.
        query: например, "стоматология"
        city: например, "Казань"
        limit: сколько компаний собрать
        """
        print(f"🔍 Ищем '{query}' в городе '{city}' через 2GIS...")
        
        records = []
        page = 1
        page_size = min(50, limit)
        
        with httpx.Client(timeout=10.0, headers={"User-Agent": settings.USER_AGENT}) as client:
            while len(records) < limit:
                params = {
                    "q": f"{query} {city}",
                    "page": page,
                    "page_size": page_size,
                    "fields": "items.contact_groups,items.adm_div",
                    "key": self.key,
                }
                
                try:
                    response = client.get(self.api_url, params=params)
                    
                    if response.status_code != 200:
                        print(f"❌ Ошибка 2GIS API: {response.status_code}")
                        break
                        
                    data = response.json()
                    
                    if "result" not in data or "items" not in data["result"]:
                        print("ℹ️ Больше результатов нет.")
                        break
                        
                    items = data["result"]["items"]
                    if not items:
                        break
                        
                    for item in items:
                        if len(records) >= limit:
                            break
                            
                        name = item.get("name", "Unknown")
                        address = item.get("address_name")
                        
                        # Достаем сайт
                        website = None
                        contacts = item.get("contact_groups", [])
                        for contact_group in contacts:
                            for contact in contact_group.get("contacts", []):
                                if contact.get("type") == "website":
                                    website = contact.get("value")
                                    # Чистим UTM-метки, если есть
                                    if website and "?" in website:
                                        website = website.split("?")[0]
                                    break
                            if website:
                                break
                                
                        # Если сайта нет вообще - тоже берем (может быть полезно для продажи "с нуля")
                        # Но сейчас нам интереснее те, у кого он есть
                        
                        record = BusinessRecord(
                            business_name=name,
                            category=query,
                            city=city,
                            address=address,
                            website=website,
                            source="2gis"
                        )
                        records.append(record)
                        
                    page += 1
                    
                    # Делаем паузу, чтобы не злить 2GIS
                    time.sleep(random.uniform(settings.MIN_PAUSE_SEC, settings.MAX_PAUSE_SEC))
                    
                except Exception as e:
                    print(f"❌ Ошибка при сборе: {e}")
                    break
                    
        print(f"✅ Собрано {len(records)} компаний из 2GIS.")
        return records