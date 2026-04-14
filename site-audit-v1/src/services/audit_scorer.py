from typing import Optional
from src.models import AuditRecord, AuditResult, SiteStatusType


def compute_audit_result(audit: AuditRecord) -> AuditResult:
    """
    Вычисляет результат аудита на основе данных AuditRecord.
    
    Args:
        audit: Запись аудита с сырыми данными
        
    Returns:
        AuditResult с подсчитанными баллами и человеко-понятными заметками
    """
    # mobile_score (0-100)
    mobile_score = 0
    if audit.has_viewport:
        mobile_score += 40
    if audit.has_phone_link:
        mobile_score += 30
    if audit.load_time_ms is not None and audit.load_time_ms < 3000:
        mobile_score += 30
    
    # ux_score (0-100)
    ux_score = 0
    if audit.has_title:
        ux_score += 30
    if audit.has_h1:
        ux_score += 30
    if audit.text_length and 100 < audit.text_length < 3000:
        ux_score += 20
    if audit.broken_layout_signals == 0:
        ux_score += 20
    
    # trust_score (0-100)
    trust_score = 0
    if audit.has_https:
        trust_score += 40
    if audit.address or audit.phone:
        trust_score += 30
    if audit.image_count and audit.image_count > 0:
        trust_score += 30
    
    # cta_score (0-100)
    cta_score = 0
    if audit.has_cta_form:
        cta_score += 50
    if audit.has_whatsapp_link or audit.has_telegram_link:
        cta_score += 25
    if audit.has_phone_link:
        cta_score += 25
    
    # overall_score
    overall_score = int((mobile_score + ux_score + trust_score + cta_score) / 4)
    
    # Генерация заметок о конверсии
    problems = []
    
    if not audit.has_viewport:
        problems.append("с телефона сайт выглядит неудобно, нет мобильной версии")
    if not audit.has_phone_link:
        problems.append("телефон нельзя нажать с мобильного, часть звонков теряется")
    if not audit.has_cta_form:
        problems.append("нет формы заявки, некуда оставить контакт")
    if not audit.has_whatsapp_link and not audit.has_telegram_link:
        problems.append("нет кнопок whatsapp/telegram, хотя большинству проще написать, чем звонить")
    if not audit.has_https:
        problems.append("нет https, браузер может помечать сайт как небезопасный")
    if audit.site_status in ("dead", "error"):
        problems.append("сайт иногда не открывается или даёт ошибку")
    
    if not problems:
        notes = "критичных дыр не найдено, но есть потенциал для улучшения конверсии."
    else:
        notes = "куда утекает конверсия: " + "; ".join(problems)
    
    return AuditResult(
        site_url=audit.website or audit.final_url or "",
        site_status=audit.site_status,
        mobile_score=mobile_score,
        ux_score=ux_score,
        trust_score=trust_score,
        cta_score=cta_score,
        overall_score=overall_score,
        notes=notes,
    )