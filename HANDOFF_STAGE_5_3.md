# TZ Stage 5.3: marketing_grade — отделение pro-фото от любительских

> **Проблема:** Vision правильно распознаёт ЧТО на фото, но не оценивает ПРИГОДНОСТЬ для лендинга.
> Проходят: кресло в полиэтиленовой плёнке (новый товар), босые ноги на сером полотенце (бытовое фото), любительский маникюр с непрорабоанной кутикулой на красном диване.
>
> **Корневая причина:** `quality.score` = техническое качество (резкость+свет+композиция). Не учитывает «продаст ли это услугу салона». Sharp+well-lit может быть и у фото клиентки в постель после маникюра.
>
> **Решение:** новая Vision-метрика `marketing_grade` (1-10) с явными критериями + вес в композитном score сортировки. Один промпт-патч, одна формула. Без новых API-вызовов.

---

## Patch A — добавить `marketing_grade` в Vision-промпт (`llm/curator.py`)

### A.1 Расширить JSON-схему ответа `analyze_photo_rich`

В существующую схему добавить новый блок (после `quality`, перед `content`):

```
"marketing_grade": {
    "score": 1-10,
    "tier": "pro_studio" | "pro_salon" | "casual_acceptable" | "amateur_outdoor" | "amateur_home" | "unusable",
    "reasons": ["short_reason_1", "short_reason_2"]
}
```

### A.2 Расширить prompt с критериями

В тексте промпта, **сразу после ROLE-SPECIFIC FIT SCORING**, добавить новый блок:

```
============================================================
MARKETING GRADE — оценка пригодности для лендинга, отдельно от технического quality.

Это НЕ о резкости/свете. Это о том, можно ли это фото показать клиенту на сайте салона
без потери доверия.

Шкала 1-10 + tier:

10 — pro_studio:
  Студийное освещение. Чистый/нейтральный фон. Композиция выстроена. Услуга — главный субъект.
  Никаких бытовых деталей. Подходит для любого блока лендинга включая hero.

8-9 — pro_salon:
  Снято в салоне на хорошую камеру/телефон с хорошим светом. Видна профессиональная среда
  (рабочее место, оборудование, продукты). Композиция продуманная. Никаких бытовых конфликтов.

6-7 — casual_acceptable:
  Снято в салоне или на природе. Освещение норм, фон нейтральный. Не идеально, но клиента
  не оттолкнёт. Нет бытовых деталей которые «убивают» доверие.

4-5 — amateur_outdoor:
  Снято в неподходящем месте (улица, парковка, дома) но без явных косяков. Может работать
  как backup, не основной выбор.

2-3 — amateur_home:
  Бытовая обстановка: диван, постель, полотенце вместо профессионального белья, личные вещи
  в кадре, домашний свет. Видны непрофессиональные детали (мозоли, неровная кутикула,
  непрорабоанные ногти, морщины крупным планом без ретуши). Фото с ощущением «снято на
  телефон после процедуры для отчётности», а не для маркетинга.

1 — unusable:
  Полиэтиленовая плёнка на новой мебели (товар не распакован). Стройка, монтаж, мусор в кадре.
  Личные предметы случайно попали в кадр (кошельки, телефоны, бутылки). Постельное бельё.
  Грязный пол. Любые «не маркетинговые» детали которые портят образ салона.

ОБЯЗАТЕЛЬНО проставь tier из списка выше. Не выдумывай свои тиры.
В reasons укажи 1-2 короткие причины (через подчёркивание, без пробелов):
  primer: ["plastic_wrap_on_chair", "unfinished_setup"]
  primer: ["bare_feet_on_towel", "home_setting"]
  primer: ["clean_lighting", "neutral_background"]
  primer: ["studio_quality", "perfect_composition"]
```

### A.3 Дополнить hard-reject правила

В существующий блок HARD REJECT в промпте добавить:

```
- marketing_grade <= 1 (unusable):
    ANY of: новая мебель в полиэтиленовой плёнке, монтаж/стройка/ремонт в кадре,
    мусор/грязь, посторонние личные предметы (телефон, кошелёк, бутылки),
    постельное бельё в кадре, поздравительные надписи во весь кадр.
    Set rejected=true with reject_reason="not_marketable".
```

---

## Patch B — обновить validator `_validate_rich_profile`

```python
# Добавить дефолты для marketing_grade
mg = data.get("marketing_grade") or {}
mg.setdefault("score", 5)         # neutral fallback
mg.setdefault("tier", "casual_acceptable")
mg.setdefault("reasons", [])
data["marketing_grade"] = mg
```

Backward-compat: старые кэшированные профили без `marketing_grade` получат score=5 (нейтрально), не сломают логику.

---

## Patch C — композитный score в `enrichment/photos_v2.py`

### C.1 Новая хелпер-функция

```python
def composite_score(profile: Dict[str, Any], block: str) -> float:
    """
    Финальный score для сортировки в блоке: fit_score (роль) + marketing_grade (вес 0.4).

    Логика:
    - Хорошее фото для роли + хороший marketing = высший приоритет
    - Высокий fit, но низкий marketing (бытовое фото пусть и в тему) — теряет позиции
    - Среднее фото с pro-марекетингом обыграет идеальное по теме, но любительское
    """
    fit = ((profile.get("fit_scores") or {}).get(block) or {}).get("score") or 0
    mg = (profile.get("marketing_grade") or {}).get("score") or 5
    return fit + mg * 0.4
```

### C.2 Использовать в `lay_out_blocks`

Заменить текущий sort:

```python
# Было:
filtered.sort(
    key=lambda c: ((c.get("profile") or {}).get("fit_scores") or {}).get(block, {}).get("score") or 0,
    reverse=True,
)

# Стало:
filtered.sort(
    key=lambda c: composite_score(c.get("profile") or {}, block),
    reverse=True,
)
```

### C.3 Обновить `pick_gallery_with_quotas`

Внутри функции — заменить ключ сортировки на composite_score (тот же принцип). Это даст правильный приоритет в условиях квот.

### C.4 Обновить `services_sort_key`

Добавить marketing_grade в композит:

```python
def services_sort_key(c):
    p = c.get("profile") or {}
    fit = ((p.get("fit_scores") or {}).get("services") or {}).get("score") or 0
    mg = (p.get("marketing_grade") or {}).get("score") or 5
    cat = p.get("category") or ""
    cat_bonus = {"work_result": 1.5, "master_at_work": -0.5}.get(cat, 0)
    return fit + cat_bonus + mg * 0.4
```

### C.5 Hard-gate по marketing_grade

В `lay_out_blocks` добавить отсечение **до** sort:

```python
# В цикле фильтрации, после rejected/screenshot/face_covered:
mg_score = (p.get("marketing_grade") or {}).get("score") or 5
if mg_score <= 2:
    continue  # любительское дно — не пускаем никуда
```

---

## Patch D — диагностика в `photo_map.json.stats`

Добавить распределение marketing_grade по тирам:

```python
stats["marketing_grade_distribution"] = {
    "pro_studio": 0,
    "pro_salon": 0,
    "casual_acceptable": 0,
    "amateur_outdoor": 0,
    "amateur_home": 0,
    "unusable": 0,
}
# Считается по всем не-rejected фото в финальной раскладке
```

И добавить в каждую photo entry в blocks:
```python
"marketing_grade": <int>,
"marketing_tier": "<str>",
```

Чтобы при визуальном аудите можно было видеть какой grade Vision поставил.

---

## DoD

- [ ] `analyze_photo_rich` промпт обновлён, возвращает `marketing_grade`
- [ ] `_validate_rich_profile` имеет дефолты для нового поля
- [ ] `composite_score` реализован и используется в `lay_out_blocks`, `pick_gallery_with_quotas`, `services_sort_key`
- [ ] Hard-gate `marketing_grade <= 2` в `lay_out_blocks`
- [ ] `stats.marketing_grade_distribution` пишется в photo_map.json
- [ ] **Визуальная проверка на lead 16:**
  - [ ] gallery: НЕТ босых ног на полотенце
  - [ ] about: НЕТ кресла в полиэтиленовой плёнке
  - [ ] Если эти фото остались — значит Vision поставил им grade > 2, нужно пересмотреть промпт
- [ ] **Визуальная проверка на lead 26:**
  - [ ] gallery: НЕТ жемчужного маникюра с домашнего дивана
  - [ ] Сравнение с предыдущей версией: оценка должна вырасти

---

## Test commands

```powershell
cd "D:\1 KURSOR_PROJ\11 PARSER"

# Lead 16 (Алёна Роговцева)
python -m enrichment.photos_v2 16 --force

# Stats
python -c "import json; m=json.load(open('data/yandex/studiya-krasoty-aleny-rogovtsevoy-16/photo_map.json',encoding='utf-8')); print(m['stats'].get('marketing_grade_distribution')); print('---'); [print(b, '->', [(p.get('alt'), p.get('marketing_grade'), p.get('marketing_tier')) for p in d.get('photos') or []]) for b,d in m['blocks'].items()]"

# Lead 26 (MOOD)
python -m enrichment.photos_v2 26 --force
explorer "D:\1 KURSOR_PROJ\11 PARSER\public\studiya-krasoty-aleny-rogovtsevoy-16"
explorer "D:\1 KURSOR_PROJ\11 PARSER\public\mood-26"
```

---

## Notes for Codex

- Новых Vision-вызовов НЕ добавляется — только расширяется существующий ответ.
- Старый кэш профилей нужно инвалидировать (force regenerate). При запуске с `--force` очищать кэш.
- ASCII-only filenames.
- Russian text в JSON: `ensure_ascii=False`.
- Лог: при отсечении по `marketing_grade <= 2` писать `   🚫 marketing_grade={N} ({tier}) — {url}`.
