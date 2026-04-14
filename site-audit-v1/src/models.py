from typing import Optional, Literal
from pydantic import BaseModel, Field, HttpUrl


SourceType = Literal["yandex_maps", "2gis", "yell", "manual"]
SiteStatusType = Literal["unknown", "no_site", "alive", "dead", "error"]
PageQualityType = Literal["unknown", "weak", "average", "strong"]
ProjectStatusType = Literal["pending", "in_progress", "completed", "cancelled"]
OutreachStatusType = Literal["pending", "sent", "opened", "replied", "interested", "not_interested"]


class BusinessRecord(BaseModel):
    business_name: str = Field(..., description="Company name")
    category: Optional[str] = Field(default=None, description="Business category")
    city: Optional[str] = Field(default=None, description="City")
    address: Optional[str] = Field(default=None, description="Address")
    phone: Optional[str] = Field(default=None, description="Phone number")
    website: Optional[str] = Field(default=None, description="Website URL as string")
    maps_url: Optional[str] = Field(default=None, description="Source map card URL")
    source: SourceType = Field(default="manual", description="Lead source")


class SiteCheckResult(BaseModel):
    input_url: Optional[str] = None
    final_url: Optional[str] = None
    site_status: SiteStatusType = "unknown"

    http_status: Optional[int] = None
    is_reachable: bool = False
    has_https: bool = False
    has_redirect: bool = False
    redirect_count: int = 0
    load_time_ms: Optional[int] = None

    error_message: Optional[str] = None


class HtmlAnalysisResult(BaseModel):
    title: Optional[str] = None
    h1: Optional[str] = None
    meta_description: Optional[str] = None

    has_title: bool = False
    has_h1: bool = False
    has_meta_description: bool = False
    has_viewport: bool = False
    has_cta_form: bool = False
    has_phone_link: bool = False
    has_whatsapp_link: bool = False
    has_telegram_link: bool = False

    text_length: int = 0
    image_count: int = 0
    form_count: int = 0
    broken_layout_signals: int = 0


class ScoreResult(BaseModel):
    technical_score: int = 0
    outdated_score: int = 0
    lead_priority: int = 0
    page_quality: PageQualityType = "unknown"
    notes: Optional[str] = None


class AuditResult(BaseModel):
    """Results of a single website audit"""
    site_url: str = Field(..., description="URL that was audited")
    site_status: SiteStatusType = Field(..., description="Overall site status")
    mobile_score: int = Field(default=0, ge=0, le=100, description="Mobile friendliness score")
    ux_score: int = Field(default=0, ge=0, le=100, description="User experience score")
    trust_score: int = Field(default=0, ge=0, le=100, description="Trust indicators score")
    cta_score: int = Field(default=0, ge=0, le=100, description="Call-to-action clarity score")
    overall_score: int = Field(default=0, ge=0, le=100, description="Overall audit score")
    
    has_ssl: Optional[bool] = Field(default=None, description="Has SSL certificate")
    has_viewport: Optional[bool] = Field(default=None, description="Has viewport meta tag (mobile adaptation)")
    has_clickable_phone: Optional[bool] = Field(default=None, description="Has clickable phone link (tel:)")
    has_messengers: Optional[bool] = Field(default=None, description="Has messenger links (wa.me or t.me)")
    is_table_layout: Optional[bool] = Field(default=None, description="Uses table-based layout")
    load_time_sec: Optional[float] = Field(default=None, description="Page load time in seconds")
    
    notes: Optional[str] = Field(default=None, description="Additional notes or observations")


class RemakeProject(BaseModel):
    """Project for website remake/redesign"""
    business_name: str = Field(..., description="Client business name")
    niche: Optional[str] = Field(default=None, description="Business niche/category")
    city: Optional[str] = Field(default=None, description="City")
    old_site_url: Optional[str] = Field(default=None, description="Current website URL")
    new_demo_path: Optional[str] = Field(default=None, description="Path to new demo site")
    status: ProjectStatusType = Field(default="pending", description="Project status")


class OutreachMessage(BaseModel):
    """Outreach message to a potential client"""
    business_name: str = Field(..., description="Target business name")
    contact: Optional[str] = Field(default=None, description="Contact email or phone")
    channel: Optional[str] = Field(default=None, description="Outreach channel (email, telegram, etc)")
    subject: Optional[str] = Field(default=None, description="Message subject")
    body: Optional[str] = Field(default=None, description="Message body")
    status: OutreachStatusType = Field(default="pending", description="Message status")


class AuditRecord(BaseModel):
    business_name: str
    category: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    maps_url: Optional[str] = None
    source: SourceType = "manual"

    site_status: SiteStatusType = "unknown"
    final_url: Optional[str] = None
    http_status: Optional[int] = None
    is_reachable: bool = False
    has_https: bool = False
    has_redirect: bool = False
    redirect_count: int = 0
    load_time_ms: Optional[int] = None

    title: Optional[str] = None
    h1: Optional[str] = None
    meta_description: Optional[str] = None
    has_title: bool = False
    has_h1: bool = False
    has_meta_description: bool = False
    has_viewport: bool = False
    has_cta_form: bool = False
    has_phone_link: bool = False
    has_whatsapp_link: bool = False
    has_telegram_link: bool = False

    text_length: int = 0
    image_count: int = 0
    form_count: int = 0
    broken_layout_signals: int = 0

    technical_score: int = 0
    outdated_score: int = 0
    lead_priority: int = 0
    page_quality: PageQualityType = "unknown"
    notes: Optional[str] = None
    error_message: Optional[str] = None

    @classmethod
    def from_parts(
        cls,
        business: BusinessRecord,
        site_check: Optional[SiteCheckResult] = None,
        html_analysis: Optional[HtmlAnalysisResult] = None,
        score: Optional[ScoreResult] = None,
    ) -> "AuditRecord":
        site_check = site_check or SiteCheckResult()
        html_analysis = html_analysis or HtmlAnalysisResult()
        score = score or ScoreResult()

        return cls(
            business_name=business.business_name,
            category=business.category,
            city=business.city,
            address=business.address,
            phone=business.phone,
            website=business.website,
            maps_url=business.maps_url,
            source=business.source,

            site_status=site_check.site_status,
            final_url=site_check.final_url,
            http_status=site_check.http_status,
            is_reachable=site_check.is_reachable,
            has_https=site_check.has_https,
            has_redirect=site_check.has_redirect,
            redirect_count=site_check.redirect_count,
            load_time_ms=site_check.load_time_ms,

            title=html_analysis.title,
            h1=html_analysis.h1,
            meta_description=html_analysis.meta_description,
            has_title=html_analysis.has_title,
            has_h1=html_analysis.has_h1,
            has_meta_description=html_analysis.has_meta_description,
            has_viewport=html_analysis.has_viewport,
            has_cta_form=html_analysis.has_cta_form,
            has_phone_link=html_analysis.has_phone_link,
            has_whatsapp_link=html_analysis.has_whatsapp_link,
            has_telegram_link=html_analysis.has_telegram_link,
            text_length=html_analysis.text_length,
            image_count=html_analysis.image_count,
            form_count=html_analysis.form_count,
            broken_layout_signals=html_analysis.broken_layout_signals,

            technical_score=score.technical_score,
            outdated_score=score.outdated_score,
            lead_priority=score.lead_priority,
            page_quality=score.page_quality,
            notes=score.notes,
            error_message=site_check.error_message,
        )