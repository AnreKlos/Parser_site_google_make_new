from src.models import SiteCheckResult, HtmlAnalysisResult, ScoreResult


class Scorer:
    def score(
        self,
        site_check: SiteCheckResult,
        html_analysis: HtmlAnalysisResult,
    ) -> ScoreResult:
        technical_score = self._score_technical(site_check, html_analysis)
        outdated_score = self._score_outdated(site_check, html_analysis)
        lead_priority = self._score_priority(site_check, html_analysis, outdated_score)
        page_quality = self._detect_page_quality(technical_score, outdated_score, site_check)

        notes = self._build_notes(site_check, html_analysis, technical_score, outdated_score)

        return ScoreResult(
            technical_score=technical_score,
            outdated_score=outdated_score,
            lead_priority=lead_priority,
            page_quality=page_quality,
            notes=notes,
        )

    def _score_technical(
        self,
        site_check: SiteCheckResult,
        html_analysis: HtmlAnalysisResult,
    ) -> int:
        score = 0

        if site_check.site_status == "alive":
            score += 30

        if site_check.is_reachable:
            score += 20

        if site_check.has_https:
            score += 15

        if html_analysis.has_viewport:
            score += 15

        if html_analysis.has_title:
            score += 5

        if html_analysis.has_h1:
            score += 5

        if html_analysis.has_meta_description:
            score += 5

        if html_analysis.has_cta_form:
            score += 5

        return max(0, min(score, 100))

    def _score_outdated(
        self,
        site_check: SiteCheckResult,
        html_analysis: HtmlAnalysisResult,
    ) -> int:
        score = 0
        error_text = (site_check.error_message or "").lower()

        if site_check.site_status == "no_site":
            score += 70

        elif site_check.site_status == "dead":
            score += 60

        elif site_check.site_status == "error":
            if "ssl certificate error" in error_text or "certificate verify failed" in error_text:
                score += 25
            elif "timeout" in error_text:
                score += 30
            else:
                score += 40

        if site_check.site_status == "alive":
            if not site_check.has_https:
                score += 15

            if not html_analysis.has_viewport:
                score += 20

            if not html_analysis.has_title:
                score += 10

            if not html_analysis.has_h1:
                score += 10

            if not html_analysis.has_meta_description:
                score += 5

            if html_analysis.text_length < 400:
                score += 10

            if html_analysis.image_count == 0:
                score += 5

            if html_analysis.form_count == 0 and not html_analysis.has_cta_form:
                score += 10

            if not html_analysis.has_phone_link:
                score += 5

            score += min(html_analysis.broken_layout_signals * 10, 30)

        return max(0, min(score, 100))

    def _score_priority(
        self,
        site_check: SiteCheckResult,
        html_analysis: HtmlAnalysisResult,
        outdated_score: int,
    ) -> int:
        score = 0

        if site_check.site_status == "alive":
            score += 30

        if site_check.site_status == "no_site":
            score += 10

        if outdated_score >= 60:
            score += 35
        elif outdated_score >= 40:
            score += 25
        elif outdated_score >= 20:
            score += 10

        if html_analysis.has_phone_link:
            score += 10

        if html_analysis.has_whatsapp_link or html_analysis.has_telegram_link:
            score += 10

        if html_analysis.has_cta_form:
            score += 5

        return max(0, min(score, 100))

    def _detect_page_quality(
        self,
        technical_score: int,
        outdated_score: int,
        site_check: SiteCheckResult,
    ) -> str:
        if site_check.site_status in ("dead", "error", "no_site"):
            return "weak"

        if technical_score >= 75 and outdated_score <= 20:
            return "strong"

        if technical_score >= 45 and outdated_score <= 50:
            return "average"

        return "weak"

    def _build_notes(
        self,
        site_check: SiteCheckResult,
        html_analysis: HtmlAnalysisResult,
        technical_score: int,
        outdated_score: int,
    ) -> str:
        notes = []

        error_text = (site_check.error_message or "").lower()

        if site_check.site_status == "no_site":
            notes.append("Нет сайта")

        elif site_check.site_status == "dead":
            notes.append("Сайт недоступен")

        elif site_check.site_status == "error":
            if "ssl certificate error" in error_text or "certificate verify failed" in error_text:
                notes.append("Ошибка SSL сертификата")
            elif "timeout" in error_text:
                notes.append("Таймаут при проверке сайта")
            else:
                notes.append("Ошибка проверки сайта")

        if site_check.site_status == "alive":
            if not site_check.has_https:
                notes.append("Нет HTTPS")
            if not html_analysis.has_viewport:
                notes.append("Нет mobile viewport")
            if not html_analysis.has_title:
                notes.append("Нет title")
            if not html_analysis.has_h1:
                notes.append("Нет H1")
            if not html_analysis.has_cta_form:
                notes.append("Нет явной CTA-формы")
            if html_analysis.text_length < 400:
                notes.append("Мало текста")
            if html_analysis.broken_layout_signals > 0:
                notes.append("Есть сигналы старой верстки")

        notes.append(f"Tech={technical_score}")
        notes.append(f"Old={outdated_score}")

        return "; ".join(notes)