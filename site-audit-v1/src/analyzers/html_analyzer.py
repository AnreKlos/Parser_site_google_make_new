import re
from bs4 import BeautifulSoup

from src.models import HtmlAnalysisResult


class HtmlAnalyzer:
    PHONE_HREF_RE = re.compile(r"^tel:", re.IGNORECASE)
    WHATSAPP_RE = re.compile(r"wa\.me|whatsapp", re.IGNORECASE)
    TELEGRAM_RE = re.compile(r"t\.me|telegram", re.IGNORECASE)

    PHONE_TEXT_RE = re.compile(
        r"(\+7|8)?[\s\-()]?\d{3}[\s\-()]?\d{3}[\s\-()]?\d{2}[\s\-()]?\d{2}"
    )

    def analyze(self, html: str) -> HtmlAnalysisResult:
        if not html or not html.strip():
            return HtmlAnalysisResult()

        soup = BeautifulSoup(html, "lxml")

        title_tag = soup.find("title")
        h1_tag = soup.find("h1")
        meta_desc_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.IGNORECASE)})
        viewport_tag = soup.find("meta", attrs={"name": re.compile("^viewport$", re.IGNORECASE)})

        forms = soup.find_all("form")
        images = soup.find_all("img")
        links = soup.find_all("a")

        title = self._clean_text(title_tag.get_text()) if title_tag else None
        h1 = self._clean_text(h1_tag.get_text()) if h1_tag else None
        meta_description = meta_desc_tag.get("content", "").strip() if meta_desc_tag else None

        visible_text = self._extract_visible_text(soup)
        text_length = len(visible_text)

        has_phone_link = any(self._is_phone_link(link) for link in links)
        has_phone_text = self._has_phone_text(visible_text)
        has_whatsapp_link = any(self._is_whatsapp_link(link) for link in links)
        has_telegram_link = any(self._is_telegram_link(link) for link in links)

        has_cta_form = self._detect_cta_form(forms, soup)
        broken_layout_signals = self._detect_broken_layout_signals(html, soup)

        return HtmlAnalysisResult(
            title=title,
            h1=h1,
            meta_description=meta_description,
            has_title=bool(title),
            has_h1=bool(h1),
            has_meta_description=bool(meta_description),
            has_viewport=viewport_tag is not None,
            has_cta_form=has_cta_form,
            has_phone_link=(has_phone_link or has_phone_text),
            has_whatsapp_link=has_whatsapp_link,
            has_telegram_link=has_telegram_link,
            text_length=text_length,
            image_count=len(images),
            form_count=len(forms),
            broken_layout_signals=broken_layout_signals,
        )

    def _clean_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    def _extract_visible_text(self, soup: BeautifulSoup) -> str:
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        return self._clean_text(text)

    def _is_phone_link(self, link) -> bool:
        href = (link.get("href") or "").strip()
        return bool(self.PHONE_HREF_RE.search(href))

    def _has_phone_text(self, text: str) -> bool:
        return bool(self.PHONE_TEXT_RE.search(text or ""))

    def _is_whatsapp_link(self, link) -> bool:
        href = (link.get("href") or "").strip()
        return bool(self.WHATSAPP_RE.search(href))

    def _is_telegram_link(self, link) -> bool:
        href = (link.get("href") or "").strip()
        return bool(self.TELEGRAM_RE.search(href))

    def _detect_cta_form(self, forms, soup: BeautifulSoup) -> bool:
        if forms:
            return True

        text = soup.get_text(" ", strip=True).lower()
        cta_markers = [
            "оставить заявку",
            "заказать звонок",
            "записаться",
            "получить консультацию",
            "связаться",
            "обратный звонок",
        ]
        return any(marker in text for marker in cta_markers)

    def _detect_broken_layout_signals(self, html: str, soup: BeautifulSoup) -> int:
        score = 0
        html_lower = html.lower()

        if "<table" in html_lower:
            score += 1

        if "font-size:10px" in html_lower or "font-size:11px" in html_lower:
            score += 1

        if soup.find_all(string=re.compile(r"under construction|404|error", re.IGNORECASE)):
            score += 1

        if len(soup.find_all("marquee")) > 0:
            score += 2

        if len(soup.find_all("blink")) > 0:
            score += 2

        return score