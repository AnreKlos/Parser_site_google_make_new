from typing import List

from src.models import BusinessRecord


class LeadCollector:
    def collect(self, source: str, query: str, city: str, limit: int = 20) -> List[BusinessRecord]:
        source = source.strip().lower()

        if source == "2gis":
            from src.parsers.twogis_parser import TwoGISParser
            parser = TwoGISParser()
            return parser.search(query=query, city=city, limit=limit)

        if source == "yandex_maps":
            from src.parsers.yandex_parser import YandexParser
            parser = YandexParser()
            return parser.search(query=query, city=city, limit=limit)

        if source == "yell":
            from src.parsers.yell_parser import YellParser
            parser = YellParser()
            return parser.search(query=query, city=city, limit=limit)

        raise ValueError(f"Неподдерживаемый источник: {source}")
