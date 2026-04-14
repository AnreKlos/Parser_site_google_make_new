import json
from pathlib import Path
from typing import List

from src.models import AuditRecord
from src.services.audit_scorer import compute_audit_result


class JsonDebugExporter:
    """Экспортёр для debug-морды: экспортирует AuditRecord в JSON для viewer."""

    def __init__(self, output_path: Path | str = "output/leads_debug.json"):
        self.output_path = Path(output_path)

    def export(self, audits: List[AuditRecord]) -> None:
        """Экспортирует аудиты в JSON для viewer."""
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        data = []
        for audit in audits:
            # Вычисляем scores и notes через compute_audit_result
            audit_result = compute_audit_result(audit)

            data.append({
                "business_name": audit.business_name,
                "city": audit.city or "",
                "source": audit.source,
                "website": audit.website,
                "phone": audit.phone or "",
                "maps_url": audit.maps_url or "",
                "site_status": audit.site_status,
                "mobile_score": audit_result.mobile_score,
                "ux_score": audit_result.ux_score,
                "trust_score": audit_result.trust_score,
                "cta_score": audit_result.cta_score,
                "overall_score": audit_result.overall_score,
                "notes": audit_result.notes or "",
            })

        with self.output_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)