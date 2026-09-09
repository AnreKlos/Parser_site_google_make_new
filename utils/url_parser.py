#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
URL Parser — классификатор URL для парсера лидов.
Определяет тип URL (сайт, соцсеть, мессенджер) и раскладывает по правильным полям.

Использование:
    from utils.url_parser import classify_url, update_socials, is_social_url
    
    url_type, platform = classify_url("https://vk.com/salon32")
    # -> ('social', 'vk')
    
    url_type, platform = classify_url("https://salon32.ru")
    # -> ('website', None)
"""

import re
import json
from typing import Optional, Tuple

# Паттерны для определения соцсетей и мессенджеров
SOCIAL_PATTERNS = {
    'instagram': r'(instagram\.com|instagr\.am)',
    'vk': r'(vk\.com|vk\.ru|m\.vk\.com|vk\.link)',
    'telegram': r'(t\.me|telegram\.me)',
    'whatsapp': r'(wa\.me|whatsapp\.com)',
    'taplink': r'(taplink\.cc|taplink\.ws)',
    'facebook': r'(facebook\.com|fb\.com)',
    'tiktok': r'(tiktok\.com)',
    'youtube': r'(youtube\.com|youtu\.be)',
    'dikidi': r'(dikidi\.app|dikidi\.ru)',
    'yclients': r'(yclients\.com)',
    'zoon': r'(zoon\.ru)',
    'prodoctorov': r'(prodoctorov\.ru)',
    'napopravku': r'(napopravku\.ru)',
    '2gis': r'(2gis\.ru|2gis\.com)',
    'avito': r'(avito\.ru)',
    'yandex_maps': r'(yandex\.(ru|com|by|kz)/maps|maps\.yandex\.(ru|com))',
}

# Паттерны, которые точно НЕ являются сайтами салонов
NON_WEBSITE_PATTERNS = [
    r'(instagram\.com|instagr\.am)',
    r'(vk\.com|vk\.ru|m\.vk\.com)',
    r'(t\.me|telegram\.me)',
    r'(wa\.me|whatsapp\.com)',
    r'(facebook\.com|fb\.com)',
    r'(tiktok\.com)',
    r'(youtube\.com|youtu\.be)',
    r'(taplink\.cc|taplink\.ws)',
    r'(dikidi\.app|dikidi\.ru)',
    r'(yclients\.com)',
    r'(zoon\.ru)',
    r'(prodoctorov\.ru)',
    r'(napopravku\.ru)',
    r'(2gis\.ru|2gis\.com)',
    r'(avito\.ru)',
    r'(yandex\.(ru|com|by|kz)/maps|maps\.yandex\.(ru|com))',
]


def classify_url(url: str) -> Tuple[str, Optional[str]]:
    """
    Классифицирует URL по типу.
    
    Returns:
        ('social', platform) — если это соцсеть/мессенджер (platform = 'vk', 'instagram', etc.)
        ('website', None) — если это обычный сайт
        ('unknown', None) — если URL пустой или невалидный
    
    Examples:
        >>> classify_url("https://vk.com/salon32")
        ('social', 'vk')
        >>> classify_url("https://instagram.com/salon32")
        ('social', 'instagram')
        >>> classify_url("https://salon32.ru")
        ('website', None)
        >>> classify_url("")
        ('unknown', None)
    """
    if not url or not isinstance(url, str):
        return 'unknown', None
    
    url = url.strip().lower()
    
    if not url:
        return 'unknown', None
    
    # Проверяем на соцсети
    for platform, pattern in SOCIAL_PATTERNS.items():
        if re.search(pattern, url, re.IGNORECASE):
            return 'social', platform
    
    # Всё остальное — сайт
    return 'website', None


def is_social_url(url: str) -> bool:
    """Проверяет, является ли URL соцсетью/мессенджером."""
    url_type, _ = classify_url(url)
    return url_type == 'social'


def update_socials(current_socials_json: Optional[str], platform: str, url: str) -> str:
    """
    Добавляет URL в JSON-объект social_links.
    
    Args:
        current_socials_json: текущий JSON из БД (или None)
        platform: тип соцсети ('vk', 'instagram', etc.)
        url: URL для добавления
    
    Returns:
        JSON-строка с обновлёнными соцсетями
    
    Example:
        >>> update_socials(None, 'vk', 'https://vk.com/salon32')
        '{"vk": ["https://vk.com/salon32"]}'
    """
    try:
        socials = json.loads(current_socials_json) if current_socials_json else {}
    except (json.JSONDecodeError, TypeError):
        socials = {}
    
    if not isinstance(socials, dict):
        socials = {}
    
    if platform not in socials:
        socials[platform] = []
    
    # Избегаем дубликатов
    if url not in socials[platform]:
        socials[platform].append(url)
    
    return json.dumps(socials, ensure_ascii=False)


def extract_all_socials_from_text(text: str) -> dict:
    """
    Извлекает все URL из текста и классифицирует их.
    Полезно для парсинга описаний и комментариев.
    
    Returns:
        {'website': [...], 'social': {'vk': [...], 'instagram': [...]}}
    """
    if not text:
        return {'website': [], 'social': {}}
    
    # Находим все URL в тексте
    url_pattern = r'https?://[^\s<>"\')\]]+'
    urls = re.findall(url_pattern, text)
    
    result = {'website': [], 'social': {}}
    
    for url in urls:
        url_type, platform = classify_url(url)
        if url_type == 'website':
            if url not in result['website']:
                result['website'].append(url)
        elif url_type == 'social':
            if platform not in result['social']:
                result['social'][platform] = []
            if url not in result['social'][platform]:
                result['social'][platform].append(url)
    
    return result


if __name__ == '__main__':
    # Тесты
    test_urls = [
        "https://vk.com/salon32",
        "https://instagram.com/strizhka32",
        "https://t.me/salon32",
        "https://wa.me/79991234567",
        "https://salon32.ru",
        "http://palchiki.com/",
        "https://taplink.cc/salon32",
        "https://dikidi.app/123",
        "https://yclients.com/123",
        "https://www.avito.ru/bryansk/predlozheniya_uslug",
        "https://yandex.ru/maps/org/mood/96195832006/",
        "",
        None,
    ]
    
    print("=== Тест classify_url ===")
    for url in test_urls:
        url_type, platform = classify_url(url)
        print(f"  {url!r:50} -> {url_type:10} {platform}")
    
    print("\n=== Тест update_socials ===")
    socials = None
    socials = update_socials(socials, 'vk', 'https://vk.com/salon32')
    print(f"  После добавления VK: {socials}")
    socials = update_socials(socials, 'instagram', 'https://instagram.com/salon32')
    print(f"  После добавления Instagram: {socials}")
    socials = update_socials(socials, 'vk', 'https://vk.com/salon32')  # Дубликат
    print(f"  После попытки дубликата VK: {socials}")
