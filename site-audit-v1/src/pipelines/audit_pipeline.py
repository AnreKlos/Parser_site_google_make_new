import sys
import time
from pathlib import Path
from typing import List

from tqdm import tqdm

from src.models import BusinessRecord, AuditRecord
from src.analyzers.site_checker import SiteChecker
from src.analyzers.html_analyzer import HtmlAnalyzer
from src.scoring.scorer import Scorer


class AuditPipeline:
    """Пайплайн для проведения аудита сайтов."""
    
    def __init__(self, delay: float = 0.0):
        """
        Инициализация пайплайна.
        
        Args:
            delay: Задержка между запросами в секундах (для соблюдения лимитов)
        """
        self.checker = SiteChecker()
        self.analyzer = HtmlAnalyzer()
        self.scorer = Scorer()
        self.delay = delay
    
    def run(self, businesses: List[BusinessRecord]) -> List[AuditRecord]:
        """
        Выполняет аудит для списка бизнес-записей.
        
        Args:
            businesses: Список BusinessRecord для аудита
            
        Returns:
            Список AuditRecord с результатами аудита
        """
        results: List[AuditRecord] = []
        total = len(businesses)
        
        if total == 0:
            return results
        
        print(f"Начинаем аудит {total} сайтов...")
        
        # Используем tqdm для отображения прогресса
        with tqdm(total=total, desc="Аудит", unit="сайт") as pbar:
            for business in businesses:
                website_short = business.website[:30] if business.website else "неизвестно"
                pbar.set_postfix_str(f"{website_short}...")
                
                try:
                    # 1. Проверка сайта
                    site_check, html = self.checker.fetch_html(business.website)
                    
                    # 2. Анализ HTML
                    html_analysis = self.analyzer.analyze(html or "")
                    
                    # 3. Оценка
                    score = self.scorer.score(site_check, html_analysis)
                    
                    # 4. Сборка AuditRecord
                    audit = AuditRecord.from_parts(
                        business=business,
                        site_check=site_check,
                        html_analysis=html_analysis,
                        score=score,
                    )
                    
                    results.append(audit)
                    pbar.update(1)
                    
                except Exception as e:
                    print(f"❌ Ошибка при аудите {business.website}: {e}", file=sys.stderr)
                    # Создаём запись об ошибке
                    error_audit = AuditRecord.from_parts(
                        business=business,
                        site_check=None,
                        html_analysis=None,
                        score=None,
                    )
                    error_audit.error_message = str(e)
                    results.append(error_audit)
                    pbar.update(1)
                
                finally:
                    # Задержка между запросами (если задана)
                    if self.delay > 0:
                        time.sleep(self.delay)
        
        success_count = len([r for r in results if r.error_message is None])
        print(f"\n✅ Аудит завершён. Успешно: {success_count}/{total}")
        return results
