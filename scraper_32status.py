#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрапер сайта 32status.ru
Парсит: прайс-лист, контакты, тексты услуг
Сохраняет результат в Markdown-файл

Настройка:
Измените CSS-селекторы в конфигурации ниже под структуру сайта.
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Dict, List, Optional
import time


class Config:
    """Конфигурация скрапера - НАСТРОЙТЕ ЭТО ПОД СВОЮ СТРУКТУРУ САЙТА"""
    
    # Базовый URL сайта
    BASE_URL = "https://32status.ru"
    
    # Пути к страницам (относительно BASE_URL)
    PAGES = {
        'price': '/price',      # Укажите правильный путь к прайс-листу
        'contacts': '/contacts',  # Укажите правильный путь к контактам
        'services': '/services',  # Укажите правильный путь к услугам
    }
    
    # CSS-селекторы для извлечения данных
    SELECTORS = {
        'price': {
            'table': 'table.prices',  # Таблица с ценами
            'rows': 'tr',              # Строки таблицы
            'cells': 'td',             # Ячейки
            'header': 'th',            # Заголовки
        },
        'contacts': {
            'container': '.contacts',  # Контейнер с контактами
            'phones': '.phone',        # Номера телефонов
            'email': '.email',         # Email
            'address': '.address',     # Адрес
            'social': '.social a',     # Соцсети
        },
        'services': {
            'items': '.service-item',  # Элементы услуг
            'title': 'h3',             # Заголовок услуги
            'description': '.desc',    # Описание
            'price': '.price',         # Цена услуги (если есть на странице услуг)
        }
    }
    
    # Заголовки для HTTP-запросов
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    # Задержка между запросами (секунды)
    REQUEST_DELAY = 1


class Scraper:
    """Основной класс скрапера"""
    
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(config.HEADERS)
        self.data = {
            'price_list': [],
            'contacts': {},
            'services': []
        }
    
    def fetch_page(self, url: str) -> Optional[BeautifulSoup]:
        """Загружает страницу и возвращает объект BeautifulSoup"""
        try:
            time.sleep(self.config.REQUEST_DELAY)
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return BeautifulSoup(response.text, 'html.parser')
        except Exception as e:
            print(f"Ошибка при загрузке {url}: {e}")
            return None
    
    def parse_price_list(self, soup: BeautifulSoup) -> List[Dict]:
        """Парсит прайс-лист"""
        prices = []
        selectors = self.config.SELECTORS['price']
        
        try:
            table = soup.select_one(selectors['table'])
            if not table:
                print("Таблица прайс-листа не найдена. Проверьте селектор 'table'")
                return prices
            
            rows = table.select(selectors['rows'])
            headers = []
            
            # Извлекаем заголовки
            header_row = rows[0] if rows else None
            if header_row:
                headers = [th.get_text(strip=True) for th in header_row.select(selectors['header'])]
            
            # Извлекаем данные
            for row in rows[1:]:  # Пропускаем заголовок
                cells = row.select(selectors['cells'])
                if len(cells) >= len(headers):
                    row_data = {}
                    for i, header in enumerate(headers):
                        if i < len(cells):
                            row_data[header] = cells[i].get_text(strip=True)
                    prices.append(row_data)
            
            print(f"Найдено {len(prices)} позиций в прайс-листе")
        except Exception as e:
            print(f"Ошибка при парсинге прайс-листа: {e}")
        
        return prices
    
    def parse_contacts(self, soup: BeautifulSoup) -> Dict:
        """Парсит контакты"""
        contacts = {}
        selectors = self.config.SELECTORS['contacts']
        
        try:
            container = soup.select_one(selectors['container'])
            if not container:
                print("Контейнер контактов не найден. Проверьте селектор 'container'")
                return contacts
            
            # Телефоны
            phones = [el.get_text(strip=True) for el in container.select(selectors['phones'])]
            if phones:
                contacts['telephones'] = phones
            
            # Email
            emails = [el.get_text(strip=True) for el in container.select(selectors['email'])]
            if emails:
                contacts['email'] = emails
            
            # Адрес
            addresses = [el.get_text(strip=True) for el in container.select(selectors['address'])]
            if addresses:
                contacts['address'] = addresses
            
            # Социальные сети
            social_links = []
            for link in container.select(selectors['social']):
                href = link.get('href', '')
                text = link.get_text(strip=True)
                if href:
                    social_links.append(f"{text}: {href}")
            if social_links:
                contacts['social_networks'] = social_links
            
            print(f"Найдено контактов: телефонов={len(phones)}, email={len(emails)}, адресов={len(addresses)}")
        except Exception as e:
            print(f"Ошибка при парсинге контактов: {e}")
        
        return contacts
    
    def parse_services(self, soup: BeautifulSoup) -> List[Dict]:
        """Парсит тексты услуг"""
        services = []
        selectors = self.config.SELECTORS['services']
        
        try:
            items = soup.select(selectors['items'])
            
            if not items:
                print("Элементы услуг не найдены. Проверьте селектор 'items'")
                return services
            
            for item in items:
                service = {}
                
                # Заголовок
                title_el = item.select_one(selectors['title'])
                if title_el:
                    service['title'] = title_el.get_text(strip=True)
                
                # Описание
                desc_el = item.select_one(selectors['description'])
                if desc_el:
                    service['description'] = desc_el.get_text(strip=True)
                
                # Цена (если есть)
                price_el = item.select_one(selectors['price'])
                if price_el:
                    service['price'] = price_el.get_text(strip=True)
                
                if service:
                    services.append(service)
            
            print(f"Найдено услуг: {len(services)}")
        except Exception as e:
            print(f"Ошибка при парсинге услуг: {e}")
        
        return services
    
    def scrape_all(self):
        """Запускает полный процесс скрапинга"""
        print("=" * 60)
        print("Начинаю скрапинг сайта 32status.ru")
        print("=" * 60)
        
        # 1. Прайс-лист
        print("\n[1/3] Парсинг прайс-листа...")
        price_url = self.config.BASE_URL + self.config.PAGES['price']
        price_soup = self.fetch_page(price_url)
        if price_soup:
            self.data['price_list'] = self.parse_price_list(price_soup)
        
        # 2. Контакты
        print("\n[2/3] Парсинг контактов...")
        contacts_url = self.config.BASE_URL + self.config.PAGES['contacts']
        contacts_soup = self.fetch_page(contacts_url)
        if contacts_soup:
            self.data['contacts'] = self.parse_contacts(contacts_soup)
        
        # 3. Услуги
        print("\n[3/3] Парсинг услуг...")
        services_url = self.config.BASE_URL + self.config.PAGES['services']
        services_soup = self.fetch_page(services_url)
        if services_soup:
            self.data['services'] = self.parse_services(services_soup)
        
        print("\n" + "=" * 60)
        print("Скрапинг завершен!")
        print(f"Результаты: прайс-лист={len(self.data['price_list'])} позиций, "
              f"контакты={len(self.data['contacts'])} полей, "
              f"услуг={len(self.data['services'])}")
        print("=" * 60)


class MarkdownExporter:
    """Экспорт данных в Markdown-файл"""
    
    @staticmethod
    def export(data: Dict, filename: str = "32status_data.md"):
        """Экспортирует данные в Markdown-файл"""
        with open(filename, 'w', encoding='utf-8') as f:
            # Заголовок
            f.write(f"# Данные с сайта 32status.ru\n\n")
            f.write(f"*Дата сбора: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}*\n\n")
            f.write("---\n\n")
            
            # Прайс-лист
            f.write("## 💰 Прайс-лист\n\n")
            if data['price_list']:
                # Создаем таблицу
                if data['price_list']:
                    headers = list(data['price_list'][0].keys())
                    f.write("| " + " | ".join(headers) + " |\n")
                    f.write("| " + " | ".join(["---"] * len(headers)) + " |\n")
                    
                    for item in data['price_list']:
                        row = " | ".join([item.get(h, '') for h in headers])
                        f.write(f"| {row} |\n")
                f.write(f"\n*Всего позиций: {len(data['price_list'])}*\n\n")
            else:
                f.write("*Прайс-лист не найден или пуст*\n\n")
            
            f.write("---\n\n")
            
            # Контакты
            f.write("## 📞 Контакты\n\n")
            if data['contacts']:
                for key, value in data['contacts'].items():
                    f.write(f"### {key.title()}\n")
                    if isinstance(value, list):
                        for item in value:
                            f.write(f"- {item}\n")
                    else:
                        f.write(f"{value}\n")
                    f.write("\n")
            else:
                f.write("*Контакты не найдены или пусты*\n\n")
            
            f.write("---\n\n")
            
            # Услуги
            f.write("## 🛠 Услуги\n\n")
            if data['services']:
                for i, service in enumerate(data['services'], 1):
                    f.write(f"### {i}. {service.get('title', 'Без названия')}\n\n")
                    
                    if 'description' in service:
                        f.write(f"{service['description']}\n\n")
                    
                    if 'price' in service:
                        f.write(f"**Цена:** {service['price']}\n\n")
                    
                    f.write("---\n\n")
            else:
                f.write("*Услуги не найдены или пусты*\n\n")
            
            # Итоговая статистика
            f.write("## 📊 Статистика\n\n")
            f.write(f"- **Позиций в прайс-листе:** {len(data['price_list'])}\n")
            f.write(f"- **Поля контактов:** {len(data['contacts'])}\n")
            f.write(f"- **Услуг:** {len(data['services'])}\n")
            f.write(f"- **Дата сбора:** {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
        
        print(f"\n✅ Данные сохранены в файл: {filename}")


def main():
    """Основная функция"""
    print("Скрапер сайта 32status.ru")
    print("=" * 60)
    print("\nПЕРЕД ЗАПУСКОМ НАСТРОЙТЕ СЕЛЕКТОРЫ В КЛАССЕ Config!")
    print("Исправьте пути (PAGES) и CSS-селекторы (SELECTORS) под структуру сайта.\n")
    
    # Создаем конфигурацию
    config = Config()
    
    # Запускаем скрапер
    scraper = Scraper(config)
    scraper.scrape_all()
    
    # Экспортируем в Markdown
    exporter = MarkdownExporter()
    exporter.export(scraper.data)
    
    print("\nГотово!")


if __name__ == "__main__":
    main()