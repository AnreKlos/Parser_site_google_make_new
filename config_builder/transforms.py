"""Config builder transforms — pure data transformation functions."""

import re
from typing import Any, Dict, List, Optional

from utils import (
    generate_neutral_service_description,
    is_junk_service_description,
    is_junk_service_title,
)


# ======================================================================
# Text / string helpers
# ======================================================================


def short_about_text(text: str, fallback: str) -> str:
    """First sentence of *text*, capped at 220 chars. Returns *fallback* if empty."""
    value = re.sub(r"\s+", " ", (text or "").strip())
    if not value:
        return fallback
    parts = re.split(r"(?<=[.!?])\s+", value)
    candidate = parts[0].strip() if parts else value
    return candidate if len(candidate) <= 220 else candidate[:217].rstrip() + "..."


def to_phone_raw(phone: str) -> str:
    """Strip everything except digits and leading ``+``."""
    return re.sub(r"[^\d+]", "", phone or "")


def format_phone(phone: str) -> str:
    """Format phone number to standard Russian format: +7 (XXX) XXX-XX-XX."""
    if not phone:
        return ""
    
    # Extract digits only
    digits = re.sub(r"[^\d]", "", phone)
    
    # If starts with 8, convert to +7
    if digits.startswith("8"):
        digits = "7" + digits[1:]
    # If starts with 7 but no +, add +
    elif digits.startswith("7") and not phone.startswith("+"):
        digits = "+" + digits
    # If no country code, assume Russia +7
    elif len(digits) == 10:
        digits = "+7" + digits
    # If starts with + already, keep it
    elif phone.startswith("+"):
        digits = "+" + re.sub(r"[^\d]", "", phone[1:])
    else:
        digits = "+7" + digits
    
    # Format as +7 (XXX) XXX-XX-XX
    clean = digits.replace("+", "")
    if len(clean) == 11:  # 7 + 10 digits
        return f"+{clean[0]} ({clean[1:4]}) {clean[4:7]}-{clean[7:9]}-{clean[9:11]}"
    
    # Fallback: return original if can't format
    return phone


def prettify_name_from_filename(filename: str) -> str:
    """Convert a filename stem to a human-readable name (max 2 words)."""
    stem = Path(filename).stem
    parts = [p for p in re.split(r"[_\-\s]+", stem) if p]
    if not parts:
        return "Мастер"
    words = [p.capitalize() for p in parts[:2]]
    return " ".join(words)


def normalize_service_name(title: str) -> str:
    """Normalize service title to human-readable nominative case.
    
    Steps:
    1. Remove price format endings: ": от", ":от", ":", " от"
    2. Apply dictionary mapping for typical patterns
    3. Capitalize first letter
    
    Examples:
    - "женской стрижки:от" -> "Женская стрижка"
    - "мужской стрижки:от" -> "Мужская стрижка"
    - "Стрижка: от" -> "Стрижка"
    """
    if not title:
        return ""
    
    title = title.strip()
    
    # Step 1: Remove price format endings (order matters)
    title = title.replace(': от', '')  # Remove ": от" first
    title = title.replace(':от', '')   # Remove ":от" second
    title = title.replace(':', '')      # Remove remaining ":"
    # Remove " от" only at the end
    if title.endswith(' от'):
        title = title[:-3]
    title = title.strip()
    title = re.sub(r'\s+', ' ', title)  # Collapse multiple spaces
    
    # Step 2: Dictionary mapping for typical patterns (case-insensitive)
    pattern_mapping = {
        'женской стрижки и укладки': 'Женская стрижка и укладка',
        'мужской стрижки и укладки': 'Мужская стрижка и укладка',
        'женской стрижки': 'Женская стрижка',
        'мужской стрижки': 'Мужская стрижка',
        'детской стрижки': 'Детская стрижка',
    }
    
    title_lower = title.lower()
    # Sort by pattern length (longer first) to match more specific patterns first
    sorted_patterns = sorted(pattern_mapping.items(), key=lambda x: len(x[0]), reverse=True)
    for pattern, replacement in sorted_patterns:
        if pattern == title_lower or title_lower.startswith(pattern + ' ') or title_lower.endswith(' ' + pattern):
            title = replacement
            break
    
    # Step 3: Capitalize first letter (if not already capitalized)
    if title:
        title = title[0].upper() + title[1:]
    
    return title


# ======================================================================
# Social links
# ======================================================================


def pick_social_links(lead: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract social-media links from lead fields and JSON social_links."""
    out: List[Dict[str, str]] = []
    social_candidates = [
        ("vk_url", "ВКонтакте", "VK"),
        ("instagram_url", "Instagram", "IG"),
        ("telegram_url", "Telegram", "TG"),
    ]
    for key, label, short in social_candidates:
        url = str(lead.get(key) or "").strip()
        if url:
            out.append({"href": url, "label": f"{label} {lead.get('name') or ''}".strip(), "short": short})

    social_raw = lead.get("social_links")
    if social_raw and isinstance(social_raw, str):
        try:
            parsed = json.loads(social_raw)
            if isinstance(parsed, dict):
                for label, short, key in (
                    ("ВКонтакте", "VK", "vk"),
                    ("Telegram", "TG", "telegram"),
                    ("Instagram", "IG", "instagram"),
                ):
                    urls = parsed.get(key) or []
                    if isinstance(urls, list):
                        for href in urls:
                            href = str(href or "").strip()
                            if href and not any(item["href"] == href for item in out):
                                out.append(
                                    {
                                        "href": href,
                                        "label": f"{label} {lead.get('name') or ''}".strip(),
                                        "short": short,
                                    }
                                )
        except Exception:
            pass
    return out


# ======================================================================
# Service merging & normalisation
# ======================================================================


def normalize_service_title(value: str) -> str:
    """Lowercase, strip special chars, collapse whitespace, remove trailing digits."""
    # Удаляем хвостовые цифры
    value = re.sub(r'\d+$', '', value or '')
    # Lowercase, strip special chars, collapse whitespace
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\sа-яё]', ' ', value.lower())).strip()


def normalize_extracted_services(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalise raw services from the extracted-payload."""
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("name") or item.get("title") or "").strip()
        if not title:
            continue
        description = str(item.get("description") or "").strip()
        price = str(item.get("price") or "").strip()
        image = str(item.get("image") or item.get("image_url") or "").strip()
        out.append(
            {
                "title": title,
                "short": title,
                "description": description or title,
                "priceFrom": price or "по запросу",
                "image": image,
            }
        )
    return out[:20]


def normalize_extracted_faq(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter FAQ pairs that have both question and answer."""
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        q = str(item.get("q") or "").strip()
        a = str(item.get("a") or "").strip()
        if q and a:
            out.append({"q": q, "a": a})
    return out[:20]


def is_junk_service_title_enhanced(title: str) -> tuple[bool, str]:
    """Расширенная проверка на мусорное название услуги.
    
    Returns (is_junk, reason)
    """
    if not title or len(title.strip()) < 2:
        return True, "пустое название"
    
    title = title.strip()
    
    # Правило 0: Длина < 5 символов → мусор (имена владельцев)
    if len(title) < 5:
        return True, f"слишком короткое название ({len(title)} символов)"
    
    # Правило 0.5: Только заглавные и короткое — вероятно имя
    if title.isupper() and len(title) < 10:
        return True, "только заглавные буквы, вероятно имя"
    
    # Правило 0.6: Список распространённых имён
    common_names = ['анна', 'мария', 'ольга', 'елена', 'ирина', 'светлана', 'наталья', 'екатерина', 'юлия', 'татьяна']
    if title.lower().strip() in common_names:
        return True, f"имя владельца: {title}"
    
    # Правило 1: Длиннее 100 символов и содержит 2+ признаков прайса
    price_indicators = 0
    if len(title) > 100:
        if '₽' in title or 'р.' in title or 'руб' in title:
            price_indicators += 1
        if '%' in title:
            price_indicators += 1
        if '–' in title or '-' in title:
            price_indicators += 1
        if re.search(r'\d+\s*[-–]\s*\d+', title):  # диапазон цен
            price_indicators += 1
        
        if price_indicators >= 2:
            return True, f"длинное название с {price_indicators} признаками прайса"
    
    # Правило 2: Начинается с фрагментов прайса (смягчённые паттерны - требуют минимум 3 цифры в цене)
    price_start_patterns = [
        r'^\s*(женской|мужской)\s+стрижки\s*:\s*\d{3,}',
        r'^\s*стрижка\s*:\s*\d{3,}',
        r'^\s*женской\s+стрижки:\d{3,}',
        r'^\s*мужской\s+стрижки:\d{3,}',
        r'^\s*окрашивание:\d{3,}',
        r'^\s*маникюр:\d{3,}',
        r'^\s*педикюр:\d{3,}',
    ]
    
    for pattern in price_start_patterns:
        if re.match(pattern, title, re.IGNORECASE):
            return True, f"начинается с фрагмента прайса: {pattern}"
    
    # Правило 2.5: Склейка бренда + цифра + услуга (Mood5Стрижка, Империя4Маникюр)
    # Паттерн: Буквы(бренд) + Цифра(рейтинг) + Буквы(услуга)
    if re.search(r'[А-Яа-яёЁA-Za-z]+\d+[А-Яа-яёЁA-Za-z]', title):
        return True, "склейка бренда+цифры+услуги"
    
    # Правило 2.6: Окончания прайс-формата без цены
    price_end_patterns = [
        r':\s*от\s*$',  # ": от" в конце
        r':\s*$',  # ":" в конце
        r':от\s*$',  # ":от" в конце
    ]
    for pattern in price_end_patterns:
        if re.search(pattern, title, re.IGNORECASE):
            return True, f"окончание прайс-формата: {pattern}"
    
    # Правило 3: Содержит склейку бренда/рейтинга/прайса
    # Пример: "Империя красоты4,4Стрижка: от"
    brand_rating_price_patterns = [
        r'[А-Яа-яёЁ\s]+[\d,]+[.,]\d+[А-Яа-яёЁ]',  # "Империя красоты4,4Стрижка" или "Империя красоты4.4Стрижка"
        r'[А-Яа-яёЁ\s]+\d+[.,]\d+\s*:',  # "Империя красоты4,4:" или "Империя красоты4.4:"
        r'[А-Яа-яёЁ\s]+:\d+[-–]',  # "Стрижка:300-"
        r'\d+[.,]\d+\s*[А-Яа-яёЁ]',  # "4.4Стрижка" или "4,4Стрижка"
    ]
    
    for pattern in brand_rating_price_patterns:
        if re.search(pattern, title):
            return True, f"склейка бренда/рейтинга/прайса: {pattern}"
    
    # Правило 4: Содержит только цифры и символы без букв
    if re.match(r'^[\d\s\-–₽%,.]+$', title):
        return True, "только цифры и символы"
    
    # Правило 5: Содержит мусорные фразы
    junk_phrases = [
        'женской стрижки:',
        'мужской стрижки:',
        'стрижки: от',
        'варьируется',
        'ВЫПОЛНЯЕТСЯ НА',
    ]
    
    for phrase in junk_phrases:
        if phrase.lower() in title.lower():
            return True, f"содержит мусорную фразу: {phrase}"
    
    return False, ""


def filter_enhanced_services(services: List[Dict[str, Any]], max_count: int = 6) -> List[Dict[str, Any]]:
    """Фильтрует мусорные услуги и ограничивает количество.
    
    Args:
        services: список услуг
        max_count: максимальное количество услуг в выводе
    
    Returns:
        Отфильтрованный список услуг
    """
    filtered = []
    junk_count = 0
    
    for service in services:
        if not isinstance(service, dict):
            continue
        
        title = service.get("title", "")
        is_junk, reason = is_junk_service_title_enhanced(title)
        
        if is_junk:
            print(f"⚠️ Пропущена мусорная услуга (config): '{title[:50]}...' ({reason})")
            junk_count += 1
            continue
        
        # Очищаем priceFrom от мусора
        price_from = service.get("priceFrom", "")
        if price_from:
            # Если priceFrom содержит мусор прайса - заменяем на уточнить при записи
            price_junk_patterns = [
                r'женской\s+стрижки:\d+[-–]',
                r'мужской\s+стрижки:\d+[-–]',
                r'стрижки:\d+[-–]\s*мужской',
                r'\d+[-–]\s*₽.*\d+[-–]',  # диапазон цен с другим диапазоном
            ]
            
            for pattern in price_junk_patterns:
                if re.search(pattern, price_from, re.IGNORECASE):
                    print(f"⚠️ Заменен мусорный priceFrom (config): '{price_from}' -> 'уточнить при записи'")
                    service["priceFrom"] = "уточнить при записи"
                    break
        
        filtered.append(service)
    
    print(f"🔍 Фильтр услуг (config): удалено {junk_count} мусорных, осталось {len(filtered)}")
    
    # Ограничиваем количество
    if len(filtered) > max_count:
        print(f"🔍 Ограничение услуг (config): {len(filtered)} -> {max_count}")
        filtered = filtered[:max_count]
    
    return filtered


def merge_services(
    curated_services: List[Dict[str, Any]],
    yandex_services: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge curated + Yandex services, enrich prices, deduplicate."""
    merged: List[Dict[str, Any]] = []
    yandex_pool = []
    for item in yandex_services:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        price = str(item.get("price") or "").strip()
        if not name:
            continue
        # Normalize service name BEFORE junk filter
        name_norm = normalize_service_name(name)
        if is_junk_service_title(name_norm):
            continue
        # Дополнительная проверка enhanced
        is_junk_enhanced, _ = is_junk_service_title_enhanced(name_norm)
        if is_junk_enhanced:
            continue
        yandex_pool.append({"name": name_norm, "price": price, "norm": name_norm})

    used_yandex = set()

    for item in curated_services:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        # Normalize curated service title
        title = normalize_service_name(title)
        short = str(item.get("short") or "").strip()
        description = str(item.get("description") or "").strip()
        price_from = str(item.get("priceFrom") or "").strip()

        if is_junk_service_description(description, title):
            description = short

        if not description or description.lower() == title.lower():
            description = generate_neutral_service_description(title)

        norm_title = title
        best_idx = None
        best_score = 0.0
        for idx, y_item in enumerate(yandex_pool):
            yn = y_item["norm"]
            if not yn:
                continue
            score = 0.0
            if norm_title in yn or yn in norm_title:
                score = 0.9
            else:
                tokens_a = set(norm_title.split())
                tokens_b = set(yn.split())
                if tokens_a and tokens_b:
                    score = len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is not None and best_score >= 0.5:
            y_item = yandex_pool[best_idx]
            used_yandex.add(best_idx)
            if y_item.get("price"):
                price_from = y_item["price"]

        merged.append(
            {
                "title": title,
                "short": short or title,
                "description": description or short or title,
                "priceFrom": price_from or "по запросу",
            }
        )

    for idx, y_item in enumerate(yandex_pool):
        if idx in used_yandex:
            continue
        name = y_item["name"]
        if is_junk_service_title(name):
            continue
        # Дополнительная проверка enhanced
        is_junk_enhanced, _ = is_junk_service_title_enhanced(name)
        if is_junk_enhanced:
            continue
        merged.append(
            {
                "title": name,
                "short": name,
                "description": generate_neutral_service_description(name),
                "priceFrom": y_item.get("price") or "по запросу",
            }
        )

    # Применяем расширенный фильтр и ограничиваем количество
    filtered_merged = filter_enhanced_services(merged, max_count=6)
    
    # Near-duplicate правило для бровей
    # Если несколько услуг содержат корень "бров", оставляем более короткий вариант
    brows_items = []
    brows_indices = []
    for idx, item in enumerate(filtered_merged):
        norm_title = normalize_service_name(item.get('title', ''))
        if 'бров' in norm_title:
            brows_items.append((idx, item, norm_title))
            brows_indices.append(idx)
    
    if len(brows_items) > 1:
        # Сортируем по длине normalized title (короче = чище)
        brows_items.sort(key=lambda x: len(x[2]))
        # Оставляем только первый (самый короткий)
        keep_idx = brows_items[0][0]
        print(f"[DEBUG] Near-duplicate brows rule: keeping '{filtered_merged[keep_idx]['title']}'")
        # Удаляем остальные
        filtered_merged = [item for idx, item in enumerate(filtered_merged) 
                          if idx not in [bi for bi, _, _ in brows_items[1:]]]
    
    return filtered_merged


# ======================================================================
# Hero helpers
# ======================================================================


def hero_brand_name(lead_name: str) -> str:
    """Short brand name for titleLine2 — strips noise words."""
    noise = {
        "студия",
        "красоты",
        "салон",
        "beauty",
        "studio",
        "center",
        "центр",
        "spa",
        "спа",
        "сервис",
        "service",
    }
    words = lead_name.strip().split()
    filtered = [w for w in words if w.lower() not in noise]
    result = " ".join(filtered).strip()
    return result if result else lead_name.strip()


def hero_line1(lead: dict) -> str:
    """Primary tagline based on category."""
    cat = (lead.get("category") or "").lower()
    if "nail" in cat:
        return "Студия маникюра"
    if "brow" in cat:
        return "Студия бровей"
    if "barber" in cat:
        return "Барбершоп"
    return "Моностудия"


def hero_line1_small(lead: dict) -> str:
    """Secondary tagline based on category."""
    cat = (lead.get("category") or "").lower()
    if "nail" in cat:
        return "идеального маникюра"
    if "brow" in cat:
        return "оформления бровей"
    if "barber" in cat:
        return "мужских стрижек"
    return "по созданию образа"


# ======================================================================
# Price section helpers
# ======================================================================


def clean_service_title(title: str) -> str:
    """
    Чистит мусорные паттерны из title перед фильтрацией.
    Возвращает очищенный title или пустую строку если очистить невозможно.
    """
    if not title or not isinstance(title, str):
        return ''
    
    # Убираем лишние пробелы и переводы строк
    title = ' '.join(title.split())
    
    # Паттерны для удаления (в порядке применения)
    patterns = [
        r'варьируется.*',  # "варьируется от до 6000р" → удалить полностью
        r'\s*от\s+\d+\s*до\s+\d+\s*₽?',  # "от 1000 до 5000₽"
        r'\s*\d+\s*₽.*$',  # цена в конце строки
        r'\d+$',  # цифра в конце ("Оформление бровей1" → "Оформление бровей")
        r'^ВЫПОЛНЯЕТСЯ.*',  # CAPS-инструкции
        r'^ОБЯЗАТЕЛЬНО.*',
        r'^ВАЖНО.*',
    ]
    
    for pattern in patterns:
        title = re.sub(pattern, '', title, flags=re.IGNORECASE)
    
    # Убираем лишние пробелы после очистки
    title = title.strip()
    
    # Capitalize первую букву (если весь title был в CAPS, он теперь нормализован)
    if title.isupper() and len(title) > 3:
        title = title.capitalize()
    
    return title


def normalize_price(raw_price: str) -> tuple[Optional[str], bool]:
    """
    Нормализует цену в формат "1 500 ₽" или "от 1 500 ₽".
    Возвращает (formatted_price, is_price_from).
    """
    if not raw_price or not isinstance(raw_price, str):
        return None, False
    
    raw = raw_price.strip()
    numbers = re.findall(r'\d+', raw)
    if not numbers:
        return None, False
    
    is_from = bool(re.match(r'^\s*от\s+\d+', raw, re.IGNORECASE))
    
    if '–' in raw or '-' in raw:
        if len(numbers) >= 2:
            # Первая половина чисел = min, вторая = max
            mid = len(numbers) // 2
            min_price = int(''.join(numbers[:mid]))
            max_price = int(''.join(numbers[mid:]))
            formatted = f"{min_price:,} – {max_price:,} ₽".replace(',', ' ')
            return formatted, False
    
    # Объединяем все числа в одно (для "1 500 ₽" → "1500")
    main_num = int(''.join(numbers))
    formatted = f"{main_num:,} ₽".replace(',', ' ')
    
    if is_from:
        formatted = f"от {formatted}"
        return formatted, True
    
    return formatted, False


def build_price_section(
    curated: dict,
    extracted: dict,
    yandex: dict,
    lead: dict,
    *,
    is_chain: bool
) -> Optional[dict]:
    """
    Собирает секцию price из доступных источников.
    Возвращает dict или None если нет валидных данных.
    """
    if is_chain:
        return None
    
    sources = [
        ('curated', curated.get('services', [])),
        ('extracted', extracted.get('serviceCarousel', [])),
        ('yandex', yandex.get('services', []))
    ]
    
    print(f"\n[DEBUG] Проверка источников:")
    for source_name, services in sources:
        print(f"  {source_name}: {len(services) if services else 0} услуг")
    
    print(f"[DEBUG] yandex dict keys: {list(yandex.keys()) if yandex else []}")
    print(f"[DEBUG] yandex.get('services'): {yandex.get('services')[:2] if yandex.get('services') else None}")
    
    items = []
    seen_titles = set()
    
    for source_name, services in sources:
        if not services:
            continue
        
        for svc in services:
            # Извлекаем поля
            raw_title = svc.get('title') or svc.get('name', '')
            title = clean_service_title(raw_title)
            # Normalize service name BEFORE junk filter
            title = normalize_service_name(title)
            raw_price = svc.get('price') or svc.get('priceFrom', '')
            description = svc.get('description') or svc.get('short', '')
            
            print(f"[DEBUG] Источник: {source_name}")
            print(f"  raw_title: {raw_title}")
            print(f"  title после clean: '{title}'")
            print(f"  raw_price: {raw_price}")
            
            # Фильтр 1: длина title
            if not title or len(title) < 4 or len(title) > 80:
                print(f"  ❌ Фильтр 1 (длина): len={len(title) if title else 0}")
                continue
            
            # Фильтр is_junk
            is_junk, junk_reason = is_junk_service_title_enhanced(title)
            if is_junk:
                print(f"  ❌ Фильтр is_junk: {junk_reason}")
                continue
            
            # Фильтр: обучающие программы — не услуги салона
            education_keywords = ['курс', 'обучение', 'мастер-класс', 'семинар', 'тренинг', 'школа']
            if any(kw in title.lower() for kw in education_keywords):
                print(f"  ❌ Фильтр: обучение (не услуга салона)")
                continue
            
            # Фильтр 3: цена должна содержать числа
            if not raw_price or not re.search(r'\d+', str(raw_price)):
                print(f"  ❌ Фильтр 3 (нет цены)")
                continue
            
            # Фильтр 4: битые форматы
            if re.match(r'^\d+–$|^:\d+|^\w+:\d+$', str(raw_price)):
                print(f"  ❌ Фильтр 4 (битый формат)")
                continue
            
            # Нормализуем цену
            formatted_price, is_from = normalize_price(str(raw_price))
            if not formatted_price:
                print(f"  ❌ normalize_price вернула None")
                continue
            
            # Дедупликация
            normalized_title = re.sub(r'[^\w\s]', '', title.lower())
            if normalized_title in seen_titles:
                print(f"  ❌ Дубликат: '{normalized_title}'")
                continue
            seen_titles.add(normalized_title)
            
            print(f"  ✅ ПРОШЛА! price={formatted_price}, priceFrom={is_from}")
            
            # Добавляем услугу
            items.append({
                'title': title.strip(),
                'price': formatted_price,
                'priceFrom': is_from,
                'duration_min': svc.get('duration_min'),
                'description': description[:120] if description else None
            })
            
            if len(items) >= 25:
                break
        
        if len(items) >= 3:
            break
    
    if not items:
        return None
    
    return {
        'enabled': True,
        'title': 'Прайс',
        'subtitle': None,
        'note': 'Окончательная стоимость уточняется при записи.',
        'groups': [
            {
                'title': None,
                'items': items
            }
        ]
    }
