"""Test script for auto_detector functionality."""
from bs4 import BeautifulSoup
from auto_detector import auto_detect

sample_html = """
<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
    <header>
        <h1>Наши услуги и цены</h1>
    </header>

    <section class="services">
        <h2>Наши услуги</h2>
        <div class="service-card">
            <h3>Разработка сайтов</h3>
            <p>Стоимость: от 15000 руб.</p>
        </div>
        <div class="service-card">
            <h3>SEO оптимизация</h3>
            <p>Стоимость: от 8000 руб.</p>
        </div>
    </section>

    <table id="price-table">
        <tr><th>Услуга</th><th>Цена</th></tr>
        <tr><td>Дизайн</td><td>5000 руб.</td></tr>
        <tr><td>Верстка</td><td>3000 руб.</td></tr>
    </table>

    <footer class="footer">
        <div class="contacts">
            <p>Телефон: <a href="tel:+74951234567">+7 (495) 123-45-67</a></p>
            <p>Email: <a href="mailto:info@example.com">info@example.com</a></p>
            <p>Адрес: ул. Примерная, д. 10, г. Москва</p>
            <a href="https://vk.com/example">ВКонтакте</a>
        </div>
    </footer>
</body>
</html>
"""

soup = BeautifulSoup(sample_html, 'lxml')
result = auto_detect(soup)

print("Auto-detection result:")
print(f"Score: {result['score']:.2f}")
print("\nSelectors:")
for key, value in result['selectors'].items():
    print(f"  {key}: {value}")
print("\nData preview:")
for key, value in result['data_preview'].items():
    print(f"  {key}: {value}")