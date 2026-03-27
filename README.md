# Скрапер сайта 32status.ru

Универсальный Python-скрипт для парсинга прайс-листа, контактов и текстов услуг с сайта 32status.ru с сохранением результата в Markdown-файл.

## 📦 Требования

- Python 3.8+
- Библиотеки: `requests`, `beautifulsoup4`

Установка зависимостей:
```bash
pip install requests beautifulsoup4
```

## 🚀 Быстрый старт

1. Настройте конфигурацию (см. ниже)
2. Запустите скрипт:
```bash
python scraper_32status.py
```

Результат будет сохранен в файл `32status_data.md`.

## ⚙️ Настройка скрапера

Откройте файл `scraper_32status.py` и настройте класс `Config` под структуру сайта:

### 1. Пути к страницам

```python
PAGES = {
    'price': '/price',      # ← измените на реальный путь к прайс-листу
    'contacts': '/contacts',  # ← измените на реальный путь к контактам
    'services': '/services',  # ← измените на реальный путь к услугам
}
```

Примеры:
- Если прайс-лист на главной: `'price': '/'`
- Если контакты по URL `/company/contacts`: `'contacts': '/company/contacts'`

### 2. CSS-селекторы

#### Для прайс-листа (таблица):

```python
SELECTORS = {
    'price': {
        'table': 'table.prices',  # CSS-селектор таблицы с ценами
        'rows': 'tr',              # Строки таблицы
        'cells': 'td',             # Ячейки данных
        'header': 'th',            # Заголовки столбцов
    },
    ...
}
```

**Как найти селекторы:**
1. Откройте страницу с прайс-листом в браузере Chrome/Firefox
2. Нажмите F12 → вкладка "Elements"
3. Найдите таблицу с ценами, кликните правой кнопкой → Copy → Copy selector
4. Вставьте в `'table'`

#### Для контактов:

```python
'contacts': {
    'container': '.contacts',  # Контейнер, содержащий все контакты
    'phones': '.phone',        # Селектор для номеров телефонов
    'email': '.email',         # Селектор для email
    'address': '.address',     # Селектор для адреса
    'social': '.social a',     # Селектор для ссылок на соцсети
}
```

#### Для услуг:

```python
'services': {
    'items': '.service-item',  # Каждый блок услуги
    'title': 'h3',             # Заголовок услуги внутри блока
    'description': '.desc',    # Описание услуги
    'price': '.price',         # Цена (если есть на странице)
}
```

### 3. Дополнительные настройки

```python
HEADERS = {
    'User-Agent': 'Mozilla/5.0 ...'  # Можно изменить при необходимости
}

REQUEST_DELAY = 1  # Задержка между запросами (секунды), увеличьте при блокировках
```

## 🔍 Как определить правильные селекторы

1. **Откройте нужную страницу в браузере**
2. **Нажмите F12** (Инструменты разработчика)
3. **Перейдите на вкладку "Elements"**
4. **Найдите нужный элемент** (таблицу, блок с телефоном и т.д.)
5. **Кликните правой кнопкой** на элементе → **Copy** → **Copy selector**
6. **Вставьте** в соответствующий параметр в `Config.SELECTORS`

**Пример:**
- HTML: `<div class="price-list"><table class="table">...</table></div>`
- Селектор для таблицы: `'table.table'` или `'.price-list table'`

## 📋 Структура вывода

Скрипт создает Markdown-файл со следующими разделами:

1. **💰 Прайс-лист** - таблица со всеми позициями
2. **📞 Контакты** - телефоны, email, адрес, соцсети
3. **🛠 Услуги** - список услуг с описаниями и ценами
4. **📊 Статистика** - количество найденных элементов

## 🐛 Устранение неполадок

### Ошибка: "Таблица прайс-листа не найдена"
- Проверьте правильность пути в `PAGES['price']`
- Проверьте селектор `SELECTORS['price']['table']`
- Убедитесь, что страница загружается (проверьте URL в браузере)

### Ошибка: "Контейнер контактов не найден"
- Проверьте путь к странице контактов
- Проверьте селектор `container` - он должен указывать на блок, содержащий все контакты

### Пустые результаты
- Увеличьте `REQUEST_DELAY` до 2-3 секунд
- Проверьте, не загружается ли сайт через JavaScript (тогда нужен Selenium)
- Проверьте, нет ли капчи или защиты от ботов

### Если сайт использует JavaScript
Если контент подгружается динамически через JavaScript, используйте Selenium вместо requests:

```python
# Замените fetch_page на:
from selenium import webdriver
from selenium.webdriver.common.by import By

def fetch_page(self, url: str):
    driver = webdriver.Chrome()
    driver.get(url)
    time.sleep(3)  # Ждем загрузки JS
    return BeautifulSoup(driver.page_source, 'html.parser')
```

## 📝 Пример настройки

Предположим, структура сайта:
- Прайс-лист: `https://32status.ru/prices/` - таблица с классом `price-table`
- Контакты: `https://32status.ru/contacts/` - блок с классом `contact-info`, телефоны в `<span class="tel">`
- Услуги: `https://32status.ru/uslugi/` - карточки с классом `service-card`

Настройка:

```python
PAGES = {
    'price': '/prices/',
    'contacts': '/contacts/',
    'services': '/uslugi/',
}

SELECTORS = {
    'price': {
        'table': 'table.price-table',
        'rows': 'tr',
        'cells': 'td',
        'header': 'th',
    },
    'contacts': {
        'container': '.contact-info',
        'phones': '.tel',
        'email': '.email',
        'address': '.address',
        'social': '.social-links a',
    },
    'services': {
        'items': '.service-card',
        'title': 'h2',
        'description': '.service-desc',
        'price': '.service-price',
    }
}
```

## 📄 Лицензия

Создано для парсинга сайта 32status.ru. Используйте в соответствии с `robots.txt` и условиями сайта.