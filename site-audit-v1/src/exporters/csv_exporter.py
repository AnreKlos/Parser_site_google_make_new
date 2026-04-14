import csv
from pathlib import Path
from typing import List, Optional

from src.models import AuditRecord
from config.settings import settings


class CsvExporter:
    def __init__(self, output_path: Optional[str | Path] = None):
        self.output_path = Path(output_path or settings.DEFAULT_CSV_OUT)

    def export(self, records: List[AuditRecord]) -> None:
        if not records:
            return

        # Гарантируем, что папка существует
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        # Выбираем поля, которые пойдут в таблицу
        headers = [
            "business_name",
            "category",
            "city",
            "phone",
            "website",
            "site_status",
            "lead_priority",
            "page_quality",
            "technical_score",
            "outdated_score",
            "has_https",
            "has_viewport",
            "has_cta_form",
            "has_phone_link",
            "notes"
        ]

        file_exists = self.output_path.exists()

        with open(self.output_path, mode="a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            
            if not file_exists:
                writer.writeheader()

            for record in records:
                row = {
                    "business_name": record.business_name,
                    "category": record.category,
                    "city": record.city,
                    "phone": record.phone,
                    "website": record.website,
                    "site_status": record.site_status,
                    "lead_priority": record.lead_priority,
                    "page_quality": record.page_quality,
                    "technical_score": record.technical_score,
                    "outdated_score": record.outdated_score,
                    "has_https": "Да" if record.has_https else "Нет",
                    "has_viewport": "Да" if record.has_viewport else "Нет",
                    "has_cta_form": "Да" if record.has_cta_form else "Нет",
                    "has_phone_link": "Да" if record.has_phone_link else "Нет",
                    "notes": record.notes
                }
                writer.writerow(row)
                
        print(f"\n[+] Успешно сохранено {len(records)} записей в {self.output_path.name}")