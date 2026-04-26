import os
import json
import base64
import requests
import re
import io
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from PIL import Image

# --- НАСТРОЙКИ КОНВЕЙЕРА ---
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "gemma4:e4b"  # Твоя модель
CLIENT_SLUG = "kalinka"
TARGET_URL = "https://kalinkamalinkabr.orgs.biz/"
BASE_DIR = rf"D:\2 Clode Proj\1\neuralsync\public\{CLIENT_SLUG}"

# Фильтр по разрешению (в пикселях). Отсекаем иконки, микро-превью и мусор.
MIN_WIDTH = 400
MIN_HEIGHT = 400

# ПРОМПТ АРТ-ДИРЕКТОРА
PROMPT = """Ты — строгий арт-директор и сортировщик файлов. Твоя задача — вернуть СТРОГО валидный JSON. НИКАКОГО ТЕКСТА ДО ИЛИ ПОСЛЕ.

1. ОЦЕНКА КАЧЕСТВА (ВАЖНО!): Сначала оцени фото. Если фото мыльное, темное, размытое, имеет черные рамки по краям, водяные знаки, текст прайс-листа или выглядит "дешево" — СРАЗУ ставь папку "trash".
2. СОРТИРОВКА ХОРОШИХ ФОТО:
- "hero" — лицо крупным планом, топ-качество, эстетика (для баннера).
- "gallery" — ногти, ресницы, макияж в процессе, готовые работы.
- "about" — интерьер, баночки с кремами (предметка).
- "team" — портреты мастеров.

Имя файла (filename): 2-3 слова на английском через подчеркивание, без расширения.
Формат ответа:
{
  "folder": "имя_папки",
  "filename": "имя_файла",
  "reason": "почему ты так решил"
}"""

def clean_json(text):
    match = re.search(r'\{.*\}', text, re.DOTALL)
    return match.group(0) if match else text

def get_image_urls(url):
    print(f"🔍 Сканируем {url} ...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"❌ Ошибка доступа к сайту: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    urls = set()
    
    for img in soup.find_all('img'):
        if img.get('src'): urls.add(urljoin(url, img.get('src')))
    for a in soup.find_all('a', href=re.compile(r'\.(jpe?g|png|webp)(\?.*)?$', re.IGNORECASE)):
        if a.get('href'): urls.add(urljoin(url, a.get('href')))
    urls.update(re.findall(r'https?://[^\s<"]+\.(?:jpg|jpeg|png|webp)', response.text))
    
    return list(urls)

def process_pipeline():
    image_urls = get_image_urls(TARGET_URL)
    if not image_urls:
        print("⚠️ Картинки не найдены.")
        return
        
    print(f"🎯 Найдено {len(image_urls)} ссылок. Запускаем арт-директора...\n")
    
    for i, img_url in enumerate(image_urls, 1):
        filename_short = img_url.split('/')[-1].split('?')[0][:20]
        print(f"[{i}/{len(image_urls)}] Смотрим: {filename_short}...")
        
        # Отсекаем векторную графику сразу
        if ".svg" in img_url.lower():
            print("   ⏩ Пропуск (SVG иконка)")
            continue

        try:
            # 1. Качаем в память
            img_resp = requests.get(img_url, timeout=10)
            img_resp.raise_for_status()
            img_data = img_resp.content
            
            # 2. Проверяем РАЗРЕШЕНИЕ через Pillow (в оперативной памяти)
            try:
                image = Image.open(io.BytesIO(img_data))
                width, height = image.size
                if width < MIN_WIDTH or height < MIN_HEIGHT:
                    print(f"   ⏩ Пропуск (Мелкое разрешение: {width}x{height} px)")
                    continue
            except Exception:
                print("   ⚠️ Не удалось прочитать размеры картинки, пропускаем.")
                continue

            # 3. Кодируем для Джеммы
            b64_img = base64.b64encode(img_data).decode('utf-8')
            
            # 4. Запрос к Ollama
            payload = {
                "model": MODEL_NAME,
                "prompt": PROMPT,
                "images": [b64_img],
                "stream": False,
                "format": "json"
            }
            
            ollama_resp = requests.post(OLLAMA_URL, json=payload).json()
            result_text = ollama_resp.get("response", "")
            
            # 5. Парсинг ответа
            data = json.loads(clean_json(result_text))
            folder = data.get("folder", "trash")
            filename = data.get("filename", f"image_{i}")
            
            if folder == "trash":
                print(f"   🗑️ БРАК: {data.get('reason')}")
                continue
                
            # 6. Сохранение на диск
            target_dir = os.path.join(BASE_DIR, folder)
            os.makedirs(target_dir, exist_ok=True)
            
            ext = ".jpg"
            if "png" in img_url.lower(): ext = ".png"
            elif "webp" in img_url.lower(): ext = ".webp"
            
            final_path = os.path.join(target_dir, f"{filename}{ext}")
            
            counter = 1
            while os.path.exists(final_path):
                final_path = os.path.join(target_dir, f"{filename}_{counter}{ext}")
                counter += 1
                
            with open(final_path, 'wb') as f:
                f.write(img_data)
                
            print(f"   ✅ {folder.upper()}: {os.path.basename(final_path)} ({width}x{height} px) - {data.get('reason')}")
            
        except requests.exceptions.RequestException as e:
            # Скрываем длинные трейсы от 403 и 404 ошибок ВК
            status = e.response.status_code if e.response else "Unknown"
            print(f"   ⚠️ Ошибка скачивания (Код {status})")
        except json.JSONDecodeError:
            print("   ❌ Джемма выдала невалидный JSON, пропускаем.")
        except Exception as e:
            print(f"   ⚠️ Системная ошибка: {e}")

if __name__ == "__main__":
    process_pipeline()
    print("\n🏁 Конвейер остановлен. Все шедевры на своих местах.")