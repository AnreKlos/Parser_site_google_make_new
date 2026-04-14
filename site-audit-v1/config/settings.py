import os
from pathlib import Path
from dotenv import load_dotenv


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
OUT_DIR = DATA_DIR / "out"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
LOGS_DIR = DATA_DIR / "logs"

YANDEX_MAPS_API_KEY = os.getenv("YANDEX_MAPS_API_KEY")

for d in [DATA_DIR, RAW_DIR, OUT_DIR, SCREENSHOTS_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)


class Settings:
    # Настройки браузера/сканера
    HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
    TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "15000"))
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    # Настройки источников
    YANDEX_MAPS_ENABLED = os.getenv("YANDEX_MAPS_ENABLED", "false").lower() == "true"
    TWOGIS_ENABLED = os.getenv("TWOGIS_ENABLED", "false").lower() == "true"
    YANDEX_MAPS_API_KEY = os.getenv("YANDEX_MAPS_API_KEY", "")

    # Файлы экспорта по умолчанию
    DEFAULT_CSV_OUT = OUT_DIR / "audit_results.csv"
    DEFAULT_SQLITE_OUT = OUT_DIR / "audit_database.sqlite"

    # Паузы между запросами
    MIN_PAUSE_SEC = float(os.getenv("MIN_PAUSE_SEC", "2.0"))
    MAX_PAUSE_SEC = float(os.getenv("MAX_PAUSE_SEC", "5.0"))


settings = Settings()