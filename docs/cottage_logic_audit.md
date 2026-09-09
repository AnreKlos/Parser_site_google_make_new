# Аудит нишевой логики (коттеджи)

Дата: 2026-09-09  
Ветка: `feat/cottage-lead-prioritization`  
Код не менялся. Цель: где сейчас режется «свой сайт / не сайт / покупатель лидов».

---

## 1. Где определяется сайт / не-сайт

**Источник правды при сборе:** `utils/url_parser.py` → `classify_url()`.

- Совпал `SOCIAL_PATTERNS` → `('social', platform)`, в `website` не пишется.
- Иначе любой http(s) → `('website', None)`.
- `NON_WEBSITE_PATTERNS` в файле есть, **нигде не вызывается**.

**Радар:** `services/google_radar.py:165–178`.  
`classify_url(website)`: сайт → `Lead.website` + `status=new`; соцсеть → `social_links` + `status=no_website`; пусто → `no_website`. Без `google_maps_url` лид skip.

**Яндекс:** `enrichment/yandex.py` (~1441) и `enrichment/yandex_state.py` (DOM `classifyUrl`). Яндекс может **перезаписать** `Lead.website`. В DOM «первый внешний http, не google/yandex» считается сайтом.

**Дашборд (только UI):** `ui/admin_dashboard.py` → `_lead_has_website()` / `enrich_lead_priority()`.  
Сайт = непустой http(s). Пусто или `status=no_website` → «Лендинг». Не http → «Проверить». **Не зовёт `classify_url`.** Если в БД уже лежит avito/2gis как `website`, дашборд считает это своим сайтом.

`core/scraper.py` нишу сайта не решает: парсит донор (`services` / `auto_dealer`).  
`db/schemas.py` — только audit_logs.  
`config.py` — пути/ключи, без классификации URL.

---

## 2. Avito, 2GIS, Zoon, Яндекс, соцсети

Уже в `SOCIAL_PATTERNS` / `classify_url` (идут в `social_links`, не в сайт):

- соцсети: instagram, vk, telegram, whatsapp, taplink, facebook, tiktok, youtube
- каталоги/букинг салонов: dikidi, yclients, zoon, prodoctorov, napopravku, **2gis**

**Нет в классификаторе:**

- **Avito** (`avito.ru`) — сейчас это «свой сайт» → ложный `status=new` / маршрут «Лиды»
- **Яндекс.Карты / yandex.ru/maps** — не в `classify_url`. Карточка хранится отдельно: `yandex_maps_url` + `data/yandex/{slug}-{id}/card.json`
- **Google Maps** — обязательный URL карточки, не сайт
- агрегаторы загородки: cian, youla, profi.ru, youdo, reparu и т.п. — нет

UI-иконки (`classify_link` в дашборде) знают 2gis/zoon/yclients/dikidi/yandex — это подписи ссылок, не отбор.

---

## 3. Поля для сегментации (уже есть, без миграции)

| Поле | Где | Хватает для коттеджей? |
|------|-----|------------------------|
| `website` | Lead | да, но может быть агрегатор |
| `phone` | Lead | да |
| `google_rating`, `reviews_count` | Lead | да; отзывы путаются Google/Яндекс |
| `city`, `address` | Lead | да; районы/посёлки нет |
| `category` | Lead, пишет Радар аргументом | не авто-types Google; дефолт `other` |
| `status` | Lead | `new` / `no_website` / audited / pitched / skipped / sold / … |
| `created_at` | Lead | да |
| `social_links` | JSON | да |
| `google_maps_url`, `yandex_maps_url` | Lead | источник карточки |
| `raw_reviews` | негатив Google ≤3 | слабо для B2B |
| `ScanRegion.niche` | default «салоны красоты» | подпись скана, не сегмент лида |

Отдельного поля «покупатель лидов» нет. Прокси: есть свой сайт (`status=new` + живой `website`).

---

## 4. Можно ли без миграции: Лендинг / Покупатель лидов / Проверить

**Да.** Уже почти сделано в дашборде:

- нет своего сайта → «Лендинг» (= кандидат на сайт)
- есть свой сайт → сейчас «Лиды» (= покупатель лидов / реклама)
- иначе → «Проверить»

Не хватает только **того же `classify_url` (+ Avito)** на этапе отображения: иначе Avito-карточка = «покупатель лидов».

БД трогать не нужно: считать в `enrich_lead_priority()`, переименовать лейбл «Лиды» → «Покупатель лидов».

---

## 5. Три минимальные правки под коттеджи

1. **`utils/url_parser.py`:** в `SOCIAL_PATTERNS` добавить `avito`, `yandex.maps`, по желанию cian/youla/profi. Тогда Радар перестанет писать их в `website`.
2. **`ui/admin_dashboard.py` → `enrich_lead_priority()`:** звать `classify_url`; маршрут «Лендинг / Покупатель лидов / Проверить»; в «Почему» писать площадку (`avito`, `vk`).
3. **Сбор:** query/category Радара (`run_radar.py` / вызов `search_and_save(..., category=...)`) — не `beauty`, а загородка (УК, клининг коттеджей, септик, ландшафт). Фильтры `filter_*.py` с `beauty/cosmetology` для новой ниши не использовать.

Порог «Яндекс ≥ 30» (`filter_results.py`) для коттеджей скорее вреден — не трогать в этом шаге, просто не звать.

---

## 6. Куда править точечно

| Что | Файл:место |
|-----|------------|
| Список не-сайтов | `utils/url_parser.py:22–37` (`SOCIAL_PATTERNS`) |
| Запись статуса при сборе | `services/google_radar.py:165–178` (уже использует classify) |
| Маршрут в таблице | `ui/admin_dashboard.py:489–560` (`_lead_has_website`, `enrich_lead_priority`) |
| Фильтр маршрута | тот же файл, сайдбар ~812 |
| Категория при скане | вызов `search_and_save(..., category=)` / `ScanRegion.niche` default `db/models.py:148` |
| Старые beauty-фильтры | `filter_results.py`, `filter_candidates.py`, `filter_active.py` — не дашборд |

`core/scraper.py` и `config.py` для этого шага не нужны.
