"""Business-logic filters: junk detection, neutral descriptions."""

import re


def is_junk_service_title(title: str) -> bool:
    """Проверяет, является ли title мусорным (непубликабельным)."""
    if not title:
        return True

    title_lower = title.lower().strip()
    title_stripped = title.strip()

    if len(title_stripped) < 3:
        return True

    if re.fullmatch(r"[\d\sр₽.,]+", title_stripped):
        return True

    junk_phrases = [
        "варьируется", "от до", "выполняется",
        "на чистые", "вымытые вами волосы",
        "подробности", "уточняйте",
    ]
    for phrase in junk_phrases:
        if phrase in title_lower:
            return True

    if title_stripped.isupper():
        instruction_words = ["выполняется", "на чистые", "вымытые", "предварительно", "требуется"]
        if any(word in title_lower for word in instruction_words):
            return True

    return False


def is_junk_service_description(description: str, title: str) -> bool:
    """Проверяет, является ли description мусорным."""
    if not description:
        return False
    desc_stripped = description.strip()
    if desc_stripped.lower() == title.lower():
        return True
    if re.fullmatch(r"[\d\s\-\(\)]+", desc_stripped):
        return True
    return False


def generate_neutral_service_description(title: str) -> str:
    """Генерирует нейтральное описание услуги на основе названия."""
    title_lower = title.lower()
    if any(w in title_lower for w in ["маникюр", "педикюр", "покрытие"]):
        return "Аккуратное выполнение с учётом пожеланий по форме и цвету."
    if "окрашивание" in title_lower:
        return "Подбор оттенка и техники под тип волос и желаемый результат."
    if any(w in title_lower for w in ["стрижка", "укладка"]):
        return "Работа по форме лица и структуре волос с фиксацией результата."
    if any(w in title_lower for w in ["бров", "ресниц"]):
        return "Коррекция и оформление под естественные пропорции лица."
    if "чистк" in title_lower:
        return "Глубокая чистка с использованием профессиональных средств."
    if "макияж" in title_lower:
        return "Стойкий макияж под ваш формат: дневной, вечерний или праздничный."
    return title
