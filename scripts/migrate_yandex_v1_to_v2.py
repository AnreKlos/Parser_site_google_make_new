# scripts/migrate_yandex_v1_to_v2.py
"""
Одноразовый скрипт миграции data/yandex/{slug}-{id}.json → data/yandex/{slug}-{id}/card_v1.json
Старые файлы НЕ удаляются (переименовываются в .v1.json для отката).
"""
from pathlib import Path
import shutil

BASE = Path(__file__).parent.parent / "data" / "yandex"

def migrate():
    # Найдём все плоские .json (старый формат)
    flat_files = [p for p in BASE.glob("*.json") if not p.name.startswith(".")]
    moved = 0
    for old_path in flat_files:
        # mood-26.json -> mood-26/card_v1.json (старый формат - сохраним для совместимости)
        stem = old_path.stem  # mood-26
        new_dir = BASE / stem
        new_dir.mkdir(exist_ok=True)
        target = new_dir / "card_v1.json"
        shutil.move(str(old_path), str(target))
        moved += 1
        print(f"  {old_path.name} -> {stem}/card_v1.json")
    print(f"Migrated: {moved} files")

if __name__ == "__main__":
    migrate()
