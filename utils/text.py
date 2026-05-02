"""Text processing utilities: slugify, translit, compact, detect."""

import re
from typing import Optional

TRANSLIT_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

FALLBACK_AUTHORS = ["Мария", "Анна", "Ольга", "Елена", "Наталья", "Татьяна"]


def compact_text(text: str) -> str:
    """Collapse all whitespace into single spaces."""
    return re.sub(r"\s+", " ", (text or "").strip())


def slugify_name(name: str, lead_id: int) -> str:
    """Transliterate Russian business name to URL-safe slug."""
    lower = (name or "").strip().lower()
    translit = "".join(TRANSLIT_MAP.get(ch, ch) for ch in lower)
    translit = re.sub(r"[^a-z0-9]+", "-", translit)
    translit = re.sub(r"-+", "-", translit).strip("-")
    return translit or f"lead-{lead_id}"


def detect_city(address: Optional[str]) -> str:
    """Extract city name from a full address string."""
    if not address:
        return "вашем городе"
    parts = [p.strip() for p in str(address).split(",") if p.strip()]
    street_markers = (
        "ул", "улица", "пр-т", "проспект", "пер", "переулок",
        "шоссе", "б-р", "бул", "наб", "дом", "д.",
    )
    skip_words = {"россия", "russia", "российская федерация"}
    for part in parts:
        cleaned = re.sub(r"^г\.?\s*", "", part, flags=re.IGNORECASE).strip()
        low = cleaned.lower()
        if not cleaned or low in skip_words:
            continue
        if any(low.startswith(m) for m in street_markers):
            continue
        if "обл" in low or "район" in low or "округ" in low or "край" in low:
            continue
        if re.search(r"\d", low):
            continue
        return cleaned
    return "вашем городе"


def fallback_author_name(raw_author: Optional[str], index: int) -> str:
    """Pick a plausible Russian first name if the raw author is unusable."""
    author = compact_text(raw_author or "")
    if author and re.search(r"[А-Яа-я]", author):
        first = re.sub(r"[^А-Яа-яA-Za-z]", "", author.split()[0])
        if first:
            return first.capitalize()
    return FALLBACK_AUTHORS[index % len(FALLBACK_AUTHORS)]


def slug_to_var_name(slug: str) -> str:
    """Convert a slug to a camelCase JS variable name (e.g. 'my-shop' -> 'myShopConfig')."""
    parts = [p for p in re.split(r"[^a-zA-Z0-9]+", slug) if p]
    if not parts:
        return "leadConfig"
    first = parts[0].lower()
    rest = [p[:1].upper() + p[1:] for p in parts[1:]]
    return f"{first}{''.join(rest)}Config"
