"""I/O functions — filesystem reads, network downloads, asset copying."""

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from config import settings

RADAR_PUBLIC_DIR = settings.public_dir
NEURALSYNC_PUBLIC_DIR = Path(settings.neuralsync_root) / "public"


def log(message: str) -> None:
    """Print with immediate flush (visible in Streamlit / CLI)."""
    print(message, flush=True)


def load_lead(lead_id: int) -> Optional[Dict[str, Any]]:
    """Load a lead row from SQLite by primary key."""
    db = Path(settings.db_path)
    if not db.exists():
        return None
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return dict(row) if row else None


def read_json_if_exists(path: Path) -> Optional[Dict[str, Any]]:
    """Read a JSON file as dict, return ``None`` if missing or not a dict."""
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else None


def list_image_urls(slug: str, folder: str) -> List[str]:
    """Web-relative URLs for images in ``public/<slug>/<folder>/``."""
    root = RADAR_PUBLIC_DIR / slug / folder
    if not root.exists() or not root.is_dir():
        return []
    files = []
    for p in sorted(root.iterdir()):
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            files.append(f"/{slug}/{folder}/{p.name}")
    return files


def download_hero_photo(slug: str, photos_list: List[str]) -> Optional[str]:
    """Download the first XXL-height photo from *photos_list* to ``public/<slug>/hero/hero_1.jpg``.

    Returns a web-relative URL or ``None`` on failure.
    """
    xxl_url = None
    for url in photos_list:
        if isinstance(url, str) and "XXL_height" in url:
            xxl_url = url
            break

    if not xxl_url:
        log("⚠️ Не найден URL с XXL_height в photos")
        return None

    hero_dir = RADAR_PUBLIC_DIR / slug / "hero"
    hero_dir.mkdir(parents=True, exist_ok=True)
    output_path = hero_dir / "hero_1.jpg"

    try:
        resp = requests.get(xxl_url, timeout=30)
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(resp.content)
        log(f"📥 Hero фото скачано: {output_path}")
        return f"/{slug}/hero/hero_1.jpg"
    except Exception as exc:
        log(f"⚠️ Ошибка скачивания hero фото: {exc}")
        return None


def copy_public_assets(slug: str, no_copy: bool) -> int:
    """Copy ``public/<slug>`` to ``neuralsync/public/<slug>``.

    Returns the number of files copied (0 if skipped).
    """
    if no_copy:
        return 0
    src = RADAR_PUBLIC_DIR / slug
    dst = NEURALSYNC_PUBLIC_DIR / slug
    if not src.exists() or not src.is_dir():
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return sum(1 for p in dst.rglob("*") if p.is_file())
