"""
Test the complete parsing workflow.
"""

import sys
from pathlib import Path
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
import time
import json

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from scraper_core import run_parse
from auto_detector import auto_detect
from bs4 import BeautifulSoup

# Test HTML with price table, contacts, and services
TEST_HTML = """
<!DOCTYPE html>
<html>
<head><title>Test Site</title></head>
<body>
    <div class="content">
        <h1>Услуги и цены</h1>
        
        <div class="services">
            <div class="service-card">
                <h3>Веб-разработка</h3>
                <p>Создание сайтов на заказ</p>
            </div>
            <div class="service-card">
                <h3>SEO оптимизация</h3>
                <p>Продвижение в поисковиках</p>
            </div>
        </div>
        
        <table id="price-table">
            <tr><th>Услуга</th><th>Цена</th></tr>
            <tr><td>Лендинг</td><td>15 000 ₽</td></tr>
            <tr><td>Интернет-магазин</td><td>45 000 ₽</td></tr>
            <tr><td>Корпоративный сайт</td><td>30 000 ₽</td></tr>
        </table>
        
        <div class="contact-info">
            <p>Телефон: +7 (999) 123-45-67</p>
            <p>Email: info@example.ru</p>
            <p>Адрес: г. Москва, ул. Примерная, д. 10</p>
        </div>
    </div>
</body>
</html>
"""

class TestHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(TEST_HTML.encode('utf-8'))
    
    def log_message(self, format, *args):
        pass  # Suppress logs

def start_test_server(port=8888):
    server = HTTPServer(('localhost', port), TestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server

def test_workflow():
    print("=" * 60)
    print("Testing complete parsing workflow")
    print("=" * 60)
    
    # Start test server
    port = 8888
    server = start_test_server(port)
    time.sleep(0.5)  # Give server time to start
    
    try:
        url = f"http://localhost:{port}/"
        print(f"\n1. Testing run_parse with URL: {url}")
        
        # Clean up any previous data
        data_dir = Path("data")
        configs_dir = Path("configs")
        if data_dir.exists():
            for f in data_dir.glob("*.md"):
                f.unlink()
        if configs_dir.exists():
            for f in configs_dir.glob("*.json"):
                f.unlink()
        
        # Run parse
        result = run_parse(url)
        
        print(f"\n2. Result:")
        print(f"   - Domain: {result['domain']}")
        print(f"   - Score: {result['score']}")
        print(f"   - Output path: {result['output_path']}")
        print(f"   - Data extracted:")
        print(f"     * Prices: {len(result['data']['prices'])} items")
        for p in result['data']['prices']:
            print(f"       - {p}")
        print(f"     * Contacts: {list(result['data']['contacts'].keys())}")
        for ctype, cvals in result['data']['contacts'].items():
            for v in cvals:
                print(f"       - {ctype}: {v}")
        print(f"     * Services: {len(result['data']['services'])} items")
        for s in result['data']['services']:
            print(f"       - {s.get('name', 'N/A')}: {s.get('description', 'N/A')}")
        
        # Check output file
        output_path = Path(result['output_path'])
        if output_path.exists():
            print(f"\n3. Markdown output (first 500 chars):")
            print("-" * 60)
            content = output_path.read_text(encoding='utf-8')
            print(content[:500])
            print("-" * 60)
        
        # Check config was saved
        config_path = configs_dir / f"{result['domain']}.json"
        if config_path.exists():
            print(f"\n4. Config saved: {config_path}")
            config_data = json.loads(config_path.read_text(encoding='utf-8'))
            print(f"   - Selectors: {list(config_data.get('selectors', {}).keys())}")
            print(f"   - Score: {config_data.get('score')}")
        else:
            print(f"\n4. Config NOT saved (score likely < 0.75)")
        
        print("\n" + "=" * 60)
        print("Workflow test completed successfully!")
        print("=" * 60)
        
    finally:
        server.shutdown()

if __name__ == "__main__":
    test_workflow()