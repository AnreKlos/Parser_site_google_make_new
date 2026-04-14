import sys
from pathlib import Path
from typing import List

from config.settings import settings
from src.models import BusinessRecord, AuditRecord
from src.exporters.csv_exporter import CsvExporter
from src.exporters.json_debug_exporter import JsonDebugExporter
from src.exporters.sqlite_exporter import SQLiteExporter
from src.pipelines.audit_pipeline import AuditPipeline
from src.services.audit_scorer import compute_audit_result
from src.services.lead_collector import LeadCollector


def load_urls_from_file(file_path: str) -> list[str]:
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Файл с URL не найден: {path}")

    with path.open("r", encoding="utf-8") as f:
        lines = [line.strip() for line in f.readlines()]

    return [line for line in lines if line and not line.startswith("#")]


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return url

    if not url.startswith(("http://", "https://")):
        return f"https://{url}"

    return url


def build_business_records_from_urls(urls: List[str]) -> List[BusinessRecord]:
    records: List[BusinessRecord] = []

    for i, url in enumerate(urls, start=1):
        records.append(
            BusinessRecord(
                business_name=f"Manual Lead {i}",
                website=normalize_url(url),
                source="manual",
            )
        )

    return records


def run_audit_for_businesses(businesses: List[BusinessRecord]) -> List[AuditRecord]:
    pipeline = AuditPipeline(delay=getattr(settings, "MIN_PAUSE_SEC", 2.0))
    return pipeline.run(businesses)


def export_results_csv(records: List[AuditRecord], output_path: Path | None = None) -> None:
    exporter = CsvExporter(output_path=output_path or settings.DEFAULT_CSV_OUT)
    exporter.export(records)


def parse_args(argv: list[str]):
    params = {
        "batch_file": None,
        "single_url": None,
        "output_file": None,
        "source": None,
        "query": None,
        "city": None,
        "limit": 20,
        "sqlite": True,
    }

    i = 0
    while i < len(argv):
        arg = argv[i]

        if arg == "--batch" and i + 1 < len(argv):
            params["batch_file"] = argv[i + 1]
            i += 2
        elif arg == "--url" and i + 1 < len(argv):
            params["single_url"] = argv[i + 1]
            i += 2
        elif arg == "--output" and i + 1 < len(argv):
            params["output_file"] = argv[i + 1]
            i += 2
        elif arg == "--source" and i + 1 < len(argv):
            params["source"] = argv[i + 1]
            i += 2
        elif arg == "--query" and i + 1 < len(argv):
            params["query"] = argv[i + 1]
            i += 2
        elif arg == "--city" and i + 1 < len(argv):
            params["city"] = argv[i + 1]
            i += 2
        elif arg == "--limit" and i + 1 < len(argv):
            params["limit"] = int(argv[i + 1])
            i += 2
        elif arg == "--no-sqlite":
            params["sqlite"] = False
            i += 1
        else:
            i += 1

    return params


def main():
    args = parse_args(sys.argv[1:])

    businesses: List[BusinessRecord] = []

    if args["source"]:
        source = args["source"].strip().lower()

        if source in {"2gis", "yandex_maps", "yell"}:
            if not args["query"] or not args["city"]:
                raise ValueError(f"Для {source} нужно передать --query и --city")

            print(
                f"Ищу лиды в {source}: "
                f"query='{args['query']}', city='{args['city']}', limit={args['limit']}"
            )

            collector = LeadCollector()
            businesses = collector.collect(
                source=source,
                query=args["query"],
                city=args["city"],
                limit=args["limit"],
            )

            print(f"Найдено лидов: {len(businesses)}")

        else:
            raise ValueError(f"Источник не поддерживается: {source}")

    elif args["batch_file"] or args["single_url"]:
        urls: List[str] = []

        if args["batch_file"]:
            print(f"Загружаю URL из файла: {args['batch_file']}")
            urls.extend(load_urls_from_file(args["batch_file"]))

        if args["single_url"]:
            print(f"Добавляю одиночный URL: {args['single_url']}")
            urls.append(args["single_url"])

        businesses = build_business_records_from_urls(urls)

    else:
        print("Пример запуска:")
        print('python -m src.main --source 2gis --query "стоматология" --city "Казань" --limit 30')
        print('python -m src.main --source yandex_maps --query "стоматология" --city "Казань" --limit 30')
        print("или")
        print("python -m src.main --batch data/sites.txt")
        return

    if not businesses:
        print("Нет данных для обработки.")
        return

    if args["sqlite"]:
        sqlite_exporter = SQLiteExporter()
        sqlite_exporter.export_leads(businesses)
        print(f"Лиды сохранены в SQLite: {settings.DEFAULT_SQLITE_OUT}")

    auditable_businesses = [b for b in businesses if b.website]

    if not auditable_businesses:
        print("У найденных лидов нет сайтов для аудита.")
        return

    print(f"Начинаю аудит сайтов: {len(auditable_businesses)}")
    audit_records = run_audit_for_businesses(auditable_businesses)

    # Экспортируем debug JSON для viewer
    debug_exporter = JsonDebugExporter()
    debug_exporter.export(audit_records)
    print(f"Debug JSON сохранён: {debug_exporter.output_path}")

    # Вычисляем результаты аудита и выводим понятный отчёт для владельца бизнеса
    for audit_record in audit_records:
        audit_result = compute_audit_result(audit_record)

        print("\n=== АУДИТ КОНВЕРСИИ ===")
        print(f"Сайт: {audit_record.website}")
        print(f"Общий балл: {audit_result.overall_score}/100")

        # Короткая расшифровка по шкалам:
        print("Мобильность:", f"{audit_result.mobile_score}/100")
        print("Понятность сайта:", f"{audit_result.ux_score}/100")
        print("Доверие к бизнесу:", f"{audit_result.trust_score}/100")
        print("Захват заявок:", f"{audit_result.cta_score}/100")

        # Короткое пояснение общего смысла:
        if audit_result.overall_score < 40:
            summary = "сайт сильно теряет конверсию, клиентам неудобно и непонятно, как с вами связаться"
        elif audit_result.overall_score < 70:
            summary = "часть конверсии утекает, но базовые вещи работают, есть смысл обновить сайт"
        else:
            summary = "основные вещи на месте, можно точечно усилить конверсию"

        print("Итог:", summary)

        # Подробные заметки, которые уже сгенерировал scorer:
        if audit_result.notes:
            print("Подробнее:", audit_result.notes)

    out_path = Path(args["output_file"]) if args["output_file"] else settings.DEFAULT_CSV_OUT
    export_results_csv(audit_records, output_path=out_path)

    if args["sqlite"]:
        sqlite_exporter = SQLiteExporter()
        sqlite_exporter.export_audits(audit_records)

    print(f"\nАудит завершён. CSV: {out_path}")
    if args["sqlite"]:
        print(f"SQLite: {settings.DEFAULT_SQLITE_OUT}")


if __name__ == "__main__":
    main()