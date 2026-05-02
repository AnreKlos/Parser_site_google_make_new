# Brand System v8.1 — Hero-блок

После рефакторинга Hero использует поля:
- `titleLine1` — основной заголовок
- `cityLine` — город
- `brand.text` — название бренда

## Что изменилось

- **Удалено:** поле `titleLine2Size`. Все задачи, связанные с ним,
  неактуальны.
- **Frontend:** `D:\2 Clode Proj\1\neuralsync\src\sections\Hero\Hero.jsx`
- **Backend сборка:** `config_builder.py`, функции:
  - `_hero_brand_name()`
  - `_hero_line1()`
  - `_hero_line1_small()`
  - `download_hero_photo()`

## Hero-фото

Скачивается через `download_hero_photo()` в `public/{slug}/hero/`.
Источник — Яндекс.Карты (через `enrichment/yandex.py`).