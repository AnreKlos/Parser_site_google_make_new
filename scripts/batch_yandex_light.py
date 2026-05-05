#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Set

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from enrichment.yandex import enrich_lead, slugify_name
from db.database import get_async_session
from db.models import Lead
from sqlalchemy import select

BASE_DIR = Path(__file__).parent.parent
YANDEX_DATA_DIR = BASE_DIR / "data" / "yandex"
LOGS_DIR = BASE_DIR / "data" / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


class BatchYandexLight:
    def __init__(self, limit: int = None, force: bool = False, specific_leads: List[int] = None, from_file: str = None):
        self.limit = limit
        self.force = force
        self.specific_leads = set(specific_leads) if specific_leads else None
        self.from_file = from_file
        
        self.total_leads = 0
        self.already_had_data = 0
        self.to_process = []
        self.success_count = 0
        self.error_count = 0
        self.errors = []
        
        self.stats = {
            "source_url": 0,
            "reviews_count": 0,
            "photos_count": 0,
            "services": 0,
        }
        
        self.total_size_bytes = 0
        self.start_time = None
        self.end_time = None
        
        # Create log file
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M")
        if self.from_file:
            self.log_file = LOGS_DIR / f"batch_filtered_{timestamp_str}.log"
        else:
            self.log_file = LOGS_DIR / f"batch_yandex_light_{timestamp_str}.log"

    def log_error(self, lead_id: int, slug: str, error: str):
        """Log an error to the log file."""
        error_entry = {
            "timestamp": datetime.now().isoformat(),
            "lead_id": lead_id,
            "slug": slug,
            "error": str(error),
        }
        self.errors.append(error_entry)
        
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(error_entry, ensure_ascii=False) + "\n")

    def get_yandex_file_path(self, slug: str, lead_id: int) -> Path:
        return YANDEX_DATA_DIR / f"{slug}-{lead_id}.json"

    def has_yandex_data(self, slug: str, lead_id: int) -> bool:
        """Check if Yandex data already exists for this lead."""
        path = self.get_yandex_file_path(slug, lead_id)
        return path.exists()

    async def find_leads_to_process(self):
        """Find all leads that need Yandex light enrichment."""
        if self.from_file:
            # Read lead IDs from JSON file
            await self.find_leads_from_file()
        else:
            # Query database for leads
            await self.find_leads_from_db()

    async def find_leads_from_file(self):
        """Read lead IDs from JSON file and fetch from DB."""
        try:
            with open(self.from_file, "r", encoding="utf-8") as f:
                filtered_leads = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"❌ Ошибка чтения файла {self.from_file}: {e}")
            return
        
        lead_ids = [item["lead_id"] for item in filtered_leads if "lead_id" in item]
        self.total_leads = len(lead_ids)
        
        async with get_async_session() as session:
            for lead_id in lead_ids:
                result = await session.execute(select(Lead).where(Lead.id == lead_id))
                lead = result.scalar_one_or_none()
                
                if not lead:
                    print(f"⚠️ Лид ID={lead_id} не найден в БД")
                    continue
                
                slug = slugify_name(lead.name, lead.id)
                
                if self.has_yandex_data(slug, lead.id) and not self.force:
                    self.already_had_data += 1
                    continue
                
                self.to_process.append({
                    "id": lead.id,
                    "name": lead.name,
                    "slug": slug,
                })
                
                # Apply limit if specified
                if self.limit and len(self.to_process) >= self.limit:
                    break

    async def find_leads_from_db(self):
        """Find leads from database."""
        async with get_async_session() as session:
            query = select(Lead)
            
            # Filter by specific leads if provided
            if self.specific_leads:
                query = query.where(Lead.id.in_(self.specific_leads))
            
            result = await session.execute(query)
            leads = result.scalars().all()
            
            self.total_leads = len(leads)
            
            for lead in leads:
                slug = slugify_name(lead.name, lead.id)
                
                if self.has_yandex_data(slug, lead.id) and not self.force:
                    self.already_had_data += 1
                    continue
                
                self.to_process.append({
                    "id": lead.id,
                    "name": lead.name,
                    "slug": slug,
                })
                
                # Apply limit if specified
                if self.limit and len(self.to_process) >= self.limit:
                    break

    def update_stats(self, result: Dict[str, Any]):
        """Update statistics from enrichment result."""
        if result:
            if result.get("rating") is not None:
                self.stats["source_url"] += 1
            if result.get("reviews_count", 0) > 0:
                self.stats["reviews_count"] += 1
            if result.get("photos", 0) > 0:
                self.stats["photos_count"] += 1
            if result.get("services"):
                self.stats["services"] += 1

    def print_progress(self, current: int):
        """Print progress bar."""
        if self.start_time is None:
            return
            
        elapsed = time.time() - self.start_time
        if current > 0:
            avg_time = elapsed / current
            remaining = len(self.to_process) - current
            eta = avg_time * remaining
            eta_str = f"{int(eta // 60)} мин {int(eta % 60)} сек"
        else:
            eta_str = "..."
        
        bar_length = 20
        filled = int(bar_length * current / len(self.to_process))
        bar = "█" * filled + "░" * (bar_length - filled)
        
        current_lead = self.to_process[current - 1] if current > 0 else {"slug": "...", "id": 0}
        
        print(f"\r[{bar}] {current}/{len(self.to_process)} | Успешно: {self.success_count} | Ошибки: {self.error_count} | ETA: {eta_str}")
        print(f"Текущий: {current_lead['slug']} (lead_id: {current_lead['id']})", end="", flush=True)

    async def process_lead(self, lead_data: Dict[str, Any]) -> bool:
        """Process a single lead."""
        lead_id = lead_data["id"]
        slug = lead_data["slug"]
        
        try:
            result = await enrich_lead(lead_id, mode="light", force=self.force)
            
            if result:
                self.success_count += 1
                self.update_stats(result)
                
                # Calculate file size
                path = self.get_yandex_file_path(slug, lead_id)
                if path.exists():
                    self.total_size_bytes += path.stat().st_size
                
                return True
            else:
                self.error_count += 1
                self.log_error(lead_id, slug, "enrich_lead returned None")
                return False
                
        except Exception as e:
            self.error_count += 1
            self.log_error(lead_id, slug, str(e))
            return False

    async def run_batch(self):
        """Run the batch enrichment."""
        print("=" * 60)
        print("BATCH YANDEX LIGHT")
        if self.from_file:
            print(f"Источник: {self.from_file}")
        print("=" * 60)
        
        # Find leads to process
        print("🔍 Поиск лидов для обработки...")
        await self.find_leads_to_process()
        
        print(f"Всего лидов: {self.total_leads}")
        print(f"Уже есть данные: {self.already_had_data} (пропущено)")
        print(f"К обработке: {len(self.to_process)}")
        print()
        
        if not self.to_process:
            print("✅ Нет лидов для обработки")
            return
        
        # Process leads
        self.start_time = time.time()
        
        for idx, lead_data in enumerate(self.to_process, 1):
            self.print_progress(idx)
            await self.process_lead(lead_data)
        
        self.end_time = time.time()
        
        # Clear progress line
        print("\r" + " " * 100 + "\r", end="", flush=True)
        
        # Print statistics
        self.print_statistics()

    def print_statistics(self):
        """Print final statistics."""
        duration = self.end_time - self.start_time if self.end_time else 0
        duration_str = f"{int(duration // 60)} мин {int(duration % 60)} сек"
        size_str = f"{self.total_size_bytes / 1024:.1f} КБ"
        
        print("=" * 60)
        print("BATCH YANDEX LIGHT - СТАТИСТИКА")
        print("=" * 60)
        print(f"Всего лидов: {self.total_leads}")
        print(f"Уже были данные: {self.already_had_data} (пропущено)")
        print(f"Обработано: {len(self.to_process)}")
        print(f"Успешно: {self.success_count}")
        print(f"Ошибки: {self.error_count}")
        print()
        print("Данные собраны:")
        print(f"  source_url: {self.stats['source_url']}")
        print(f"  reviews_count: {self.stats['reviews_count']}")
        print(f"  photos_count: {self.stats['photos_count']}")
        print(f"  services: {self.stats['services']}")
        print()
        print(f"Общий вес: {size_str}")
        print(f"Время: {duration_str}")
        
        if self.errors:
            print()
            print(f"Лог ошибок: {self.log_file}")
            print(f"Ошибок: {len(self.errors)}")
        
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Batch Yandex Light enrichment")
    parser.add_argument("--leads", type=str, help="Specific lead IDs (comma-separated)")
    parser.add_argument("--from-file", type=str, help="Read lead IDs from JSON file (e.g., data/filtered_leads.json)")
    parser.add_argument("--only-missing", action="store_true", help="Only process leads without Yandex data (default)")
    parser.add_argument("--force", action="store_true", help="Reprocess all leads, overwrite existing data")
    parser.add_argument("--limit", type=int, help="Limit number of leads to process")
    
    args = parser.parse_args()
    
    # Parse specific leads if provided
    specific_leads = None
    if args.leads:
        try:
            specific_leads = [int(x.strip()) for x in args.leads.split(",")]
        except ValueError:
            print("❌ Неверный формат --leads. Используйте: --leads 20,42,58")
            sys.exit(1)
    
    # Check for conflicting arguments
    if args.from_file and args.leads:
        print("❌ Нельзя использовать --from-file и --leads одновременно")
        sys.exit(1)
    
    # Create and run batch
    batch = BatchYandexLight(
        limit=args.limit,
        force=args.force,
        specific_leads=specific_leads,
        from_file=args.from_file
    )
    
    try:
        asyncio.run(batch.run_batch())
    except KeyboardInterrupt:
        print("\n⚠️ Прервано пользователем")
        sys.exit(1)


if __name__ == "__main__":
    main()
