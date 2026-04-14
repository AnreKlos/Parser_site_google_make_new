import time
from typing import Optional, Tuple

import httpx

from config.settings import settings
from src.models import SiteCheckResult


class SiteChecker:
    def __init__(self, timeout_ms: Optional[int] = None):
        self.timeout_ms = timeout_ms or settings.TIMEOUT_MS
        self.timeout_sec = self.timeout_ms / 1000

    def normalize_url(self, url: str) -> str:
        url = (url or "").strip()

        if not url:
            return ""

        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        return url

    def check(self, url: Optional[str]) -> SiteCheckResult:
        if not url:
            return SiteCheckResult(
                input_url=url,
                site_status="no_site",
                is_reachable=False,
                error_message="No website provided",
            )

        normalized_url = self.normalize_url(url)

        start = time.perf_counter()

        try:
            with httpx.Client(
                timeout=self.timeout_sec,
                headers={"User-Agent": settings.USER_AGENT},
                follow_redirects=True,
                verify=True,
            ) as client:
                response = client.get(normalized_url)

            elapsed_ms = int((time.perf_counter() - start) * 1000)

            history = response.history or []
            final_url = str(response.url)

            is_reachable = response.status_code < 400
            has_https = final_url.startswith("https://")
            has_redirect = len(history) > 0

            site_status = "alive" if is_reachable else "dead"

            return SiteCheckResult(
                input_url=normalized_url,
                final_url=final_url,
                site_status=site_status,
                http_status=response.status_code,
                is_reachable=is_reachable,
                has_https=has_https,
                has_redirect=has_redirect,
                redirect_count=len(history),
                load_time_ms=elapsed_ms,
                error_message=None if is_reachable else f"HTTP {response.status_code}",
            )

        except httpx.ConnectTimeout:
            return SiteCheckResult(
                input_url=normalized_url,
                site_status="error",
                is_reachable=False,
                error_message="Connection timeout",
            )
        except httpx.ReadTimeout:
            return SiteCheckResult(
                input_url=normalized_url,
                site_status="error",
                is_reachable=False,
                error_message="Read timeout",
            )
        except httpx.ConnectError as e:
            error_text = str(e)

            if "CERTIFICATE_VERIFY_FAILED" in error_text or "certificate verify failed" in error_text.lower():
                return SiteCheckResult(
                    input_url=normalized_url,
                    site_status="error",
                    is_reachable=False,
                    error_message=f"SSL certificate error: {error_text}",
                )

            return SiteCheckResult(
                input_url=normalized_url,
                site_status="dead",
                is_reachable=False,
                error_message=f"Connection error: {error_text}",
            )
        except httpx.RequestError as e:
            return SiteCheckResult(
                input_url=normalized_url,
                site_status="error",
                is_reachable=False,
                error_message=f"Request error: {str(e)}",
            )

            if "CERTIFICATE_VERIFY_FAILED" in error_text or "certificate verify failed" in error_text.lower():
                return SiteCheckResult(
                    input_url=normalized_url,
                    site_status="error",
                    is_reachable=False,
                    error_message=f"SSL certificate error: {error_text}",
                )

            return SiteCheckResult(
                input_url=normalized_url,
                site_status="dead",
                is_reachable=False,
                error_message=f"Connection error: {error_text}",
            )
        except httpx.RequestError as e:
            return SiteCheckResult(
                input_url=normalized_url,
                site_status="error",
                is_reachable=False,
                error_message=f"Request error: {str(e)}",
            )

    def fetch_html(self, url: Optional[str]) -> Tuple[SiteCheckResult, Optional[str]]:
        result = self.check(url)

        if not result.is_reachable or not result.final_url:
            return result, None

        try:
            with httpx.Client(
                timeout=self.timeout_sec,
                headers={"User-Agent": settings.USER_AGENT},
                follow_redirects=True,
                verify=True,
            ) as client:
                response = client.get(result.final_url)

            if response.status_code >= 400:
                result.site_status = "dead"
                result.error_message = f"HTML fetch failed with status {response.status_code}"
                return result, None

            return result, response.text

        except Exception as e:
            result.site_status = "error"
            result.error_message = f"HTML fetch error: {str(e)}"
            return result, None