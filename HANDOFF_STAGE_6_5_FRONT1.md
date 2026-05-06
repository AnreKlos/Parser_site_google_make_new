# TZ Stage 6.5 (front 1): builder_v2 polish

> **Цель:** убрать видимые косяки данных в лендинге без переделки curator.
> Точечные фиксы в `config_builder/builder_v2.py`.

## Patch 1 — нормализация working hours (убрать AM/PM)

Сейчас в card.json `working_hours.text` приходит как `"ежедневно, 10:00 AM–8:00 PM"`. Нужен формат `"ежедневно, 10:00–20:00"`.

В `builder_v2.py` добавить хелпер и использовать его в `_build_contacts`:

```python
import re

def _format_working_hours(text: str) -> str:
    """
    Конвертирует "10:00 AM–8:00 PM" → "10:00–20:00".
    Обрабатывает: AM, PM, am, pm, A.M., P.M.
    """
    if not text:
        return ""
    
    def to_24h(match):
        hour = int(match.group(1))
        minute = match.group(2) or "00"
        meridiem = match.group(3).upper().replace(".", "")
        if meridiem == "PM" and hour != 12:
            hour += 12
        elif meridiem == "AM" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute}"
    
    pattern = r"(\d{1,2}):?(\d{2})?\s*(A\.?M\.?|P\.?M\.?)"
    return re.sub(pattern, to_24h, text, flags=re.IGNORECASE)
```

Применить в `_build_contacts`:
```python
hours_raw = ((card.get("working_hours") or {}).get("text") or "").strip()
hours_formatted = _format_working_hours(hours_raw)
contacts["workingHours"] = hours_formatted
contacts["hours"] = hours_formatted
```

## Patch 2 — склонение города в hero.lead

Сейчас `_short_about_text` возвращает `"Mood в Брянск, ..."`. Нужно `"Mood в Брянске, ..."`.

Простой словарь склонений (предложный падеж):

```python
CITY_PREPOSITIONAL = {
    "Брянск": "Брянске",
    "Москва": "Москве",
    "Санкт-Петербург": "Санкт-Петербурге",
    "Новосибирск": "Новосибирске",
    "Екатеринбург": "Екатеринбурге",
    "Казань": "Казани",
    "Нижний Новгород": "Нижнем Новгороде",
    "Челябинск": "Челябинске",
    "Самара": "Самаре",
    "Омск": "Омске",
    "Ростов-на-Дону": "Ростове-на-Дону",
    "Уфа": "Уфе",
    "Красноярск": "Красноярске",
    "Воронеж": "Воронеже",
    "Пермь": "Перми",
    "Волгоград": "Волгограде",
    "Краснодар": "Краснодаре",
    "Саратов": "Саратове",
    "Тюмень": "Тюмени",
    "Тольятти": "Тольятти",
    "Ижевск": "Ижевске",
    "Барнаул": "Барнауле",
    "Ульяновск": "Ульяновске",
    "Иркутск": "Иркутске",
    "Хабаровск": "Хабаровске",
    "Ярославль": "Ярославле",
    "Владивосток": "Владивостоке",
    "Махачкала": "Махачкале",
    "Томск": "Томске",
    "Оренбург": "Оренбурге",
    "Кемерово": "Кемерово",
    "Новокузнецк": "Новокузнецке",
    "Рязань": "Рязани",
    "Астрахань": "Астрахани",
    "Пенза": "Пензе",
    "Липецк": "Липецке",
    "Тула": "Туле",
    "Киров": "Кирове",
    "Чебоксары": "Чебоксарах",
    "Калининград": "Калининграде",
    "Курск": "Курске",
    "Ставрополь": "Ставрополе",
    "Сочи": "Сочи",
    "Орёл": "Орле",
    "Орел": "Орле",
    "Тверь": "Твери",
    "Белгород": "Белгороде",
    "Иваново": "Иваново",
    "Брянска": "Брянске",  # на случай если в card другая форма
}


def _city_prepositional(city: str) -> str:
    """Возвращает предложный падеж города. Если не нашли — возвращаем как есть."""
    if not city:
        return ""
    city = city.strip()
    return CITY_PREPOSITIONAL.get(city, city)
```

Обновить `_short_about_text` и `_build_hero_section`:

```python
def _short_about_text(card: Dict[str, Any]) -> str:
    name = card.get("title") or "Салон"
    address = card.get("address") or {}
    locality = address.get("locality") or ""
    locality_prep = _city_prepositional(locality)  # <-- здесь
    street_house = " ".join([
        address.get("street") or "",
        address.get("house") or "",
    ]).strip()
    if locality_prep and street_house:
        return f"{name} в {locality_prep}, {street_house}."
    if locality_prep:
        return f"{name} в {locality_prep}."
    return name
```

И в `_build_hero_section` — `topLabel`:
```python
city_prep = _city_prepositional(city)
"topLabel": f"{city_prep} · запись онлайн" if city else "запись онлайн",
```

И в `meta.brand.tagline` fallback (если curated пуст):
```python
city_prep = _city_prepositional(city)
tagline = f"Салон красоты в {city_prep}" if city_prep else "Салон красоты"
```

## Patch 3 — расширенный hero.lead

Сейчас hero.lead = "Mood в Брянске, Московский проспект 10/11." — голый адрес. Усилим:

```python
def _build_hero_section(card, lead, hero_image, hero_photo):
    name = card.get("title") or ""
    address = card.get("address") or {}
    city = address.get("locality") or ""
    city_prep = _city_prepositional(city)
    rating = card.get("rating")
    review_count = card.get("review_count") or card.get("rating_count") or 0
    good_place = card.get("good_place_year")
    
    # Lead — собираем из реальных данных
    lead_parts = []
    if city_prep:
        lead_parts.append(f"Студия красоты в {city_prep}")
    if rating and review_count and review_count >= 30:
        lead_parts.append(f"рейтинг {rating} на основе {review_count} отзывов")
    if good_place:
        lead_parts.append(f"награда «Хорошее место {good_place}»")
    
    lead_text = ". ".join(lead_parts) + "." if lead_parts else _short_about_text(card)
    
    return {
        "enabled": True,
        "image": hero_image,
        "imageAlt": (hero_photo or {}).get("alt") if hero_photo else "",
        "titleLine1": "Студия красоты",
        "titleLine1Small": "по созданию образа",
        "titleLine1SmallSize": "default",
        "titleLine2": _strip_brand_noise(name),
        "topLabel": f"{city_prep} · запись онлайн" if city_prep else "запись онлайн",
        "lead": lead_text,
        "ctaLabel": "Записаться",
    }
```

## DoD

- [ ] `_format_working_hours` реализован, применён в `_build_contacts`
- [ ] `CITY_PREPOSITIONAL` словарь + `_city_prepositional` хелпер
- [ ] `_short_about_text`, `_build_hero_section` (`topLabel`), `_build_meta` (tagline fallback) используют склонение
- [ ] `_build_hero_section.lead` собирается из rating/reviews/good_place
- [ ] Тест: `python -m config_builder.cli 26 --force` → проверить:
  - `contacts.workingHours` = "ежедневно, 10:00–20:00" (без AM/PM)
  - `sections.hero.lead` = "Студия красоты в Брянске. рейтинг 5.0 на основе 269 отзывов. награда «Хорошее место 2026»."
  - `sections.hero.topLabel` = "Брянске · запись онлайн"
  - `meta.brand.tagline` (если curated был — не трогать; если пуст — должно быть "Салон красоты в Брянске")

## NOT in scope

- Перегенерация curated — отдельная задача (curator переписать на card.json)
- Шаблон neuralsync (About.jsx, Services.jsx, Promotion CTA) — фронт 2
- Tagline через AI — оставляем как есть (`meta.brand.tagline` берётся из curated)
