from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from config.settings import settings
from src.models import BusinessRecord, AuditRecord


class SQLiteExporter:
    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = Path(db_path or settings.DEFAULT_SQLITE_OUT)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_name TEXT NOT NULL,
                    category TEXT,
                    city TEXT,
                    address TEXT,
                    phone TEXT,
                    website TEXT,
                    maps_url TEXT,
                    source TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS audits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_name TEXT NOT NULL,
                    website TEXT,
                    site_status TEXT,
                    final_url TEXT,
                    http_status INTEGER,
                    is_reachable INTEGER,
                    has_https INTEGER,
                    has_redirect INTEGER,
                    redirect_count INTEGER,
                    load_time_ms INTEGER,
                    technical_score INTEGER,
                    outdated_score INTEGER,
                    lead_priority INTEGER,
                    page_quality TEXT,
                    notes TEXT,
                    error_message TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            conn.commit()

    def export_leads(self, records: Iterable[BusinessRecord]) -> None:
        self.init_db()

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()

            for record in records:
                cur.execute(
                    """
                    INSERT INTO leads (
                        business_name,
                        category,
                        city,
                        address,
                        phone,
                        website,
                        maps_url,
                        source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.business_name,
                        record.category,
                        record.city,
                        record.address,
                        record.phone,
                        record.website,
                        record.maps_url,
                        record.source,
                    ),
                )

            conn.commit()

    def export_audits(self, records: Iterable[AuditRecord]) -> None:
        self.init_db()

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()

            for record in records:
                cur.execute(
                    """
                    INSERT INTO audits (
                        business_name,
                        website,
                        site_status,
                        final_url,
                        http_status,
                        is_reachable,
                        has_https,
                        has_redirect,
                        redirect_count,
                        load_time_ms,
                        technical_score,
                        outdated_score,
                        lead_priority,
                        page_quality,
                        notes,
                        error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.business_name,
                        record.website,
                        record.site_status,
                        record.final_url,
                        record.http_status,
                        int(bool(record.is_reachable)),
                        int(bool(record.has_https)),
                        int(bool(record.has_redirect)),
                        record.redirect_count,
                        record.load_time_ms,
                        record.technical_score,
                        record.outdated_score,
                        record.lead_priority,
                        record.page_quality,
                        record.notes,
                        record.error_message,
                    ),
                )

            conn.commit()