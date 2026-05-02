"""Default constants for config sections — tokens, legal, content, etc."""

DEFAULT_TOKENS = {
    "GOLD": "#C9A87A",
    "GOLD_DIM": "#A68B5A",
    "GOLD_BRIGHT": "#D4B88A",
    "TEXT": "#F0EBE3",
    "TEXT_SOFT": "#B5AFA7",
    "MUTED": "#9A938B",
    "BG": "#0E0C0B",
    "CHOCOLATE": "#151210",
    "SURFACE": "#1A1714",
    "SURFACE_L": "#262220",
    "BORDER": "rgba(255,255,255,0.06)",
    "BORDER_H": "rgba(255,255,255,0.14)",
    "EASE": [0.16, 1, 0.3, 1],
}

DEFAULT_LEGAL = {
    "showInFooter": True,
    "placeholder": "Реквизиты предоставим при заключении договора",
}

DEFAULT_CONTENT = {
    "promotion": {
        "title": "Особое предложение для новых клиентов",
        "text": "Оставьте заявку — администратор подберет подходящую услугу и удобное время визита.",
    }
}

DEFAULT_CHAT_WIDGET = {
    "enabled": True,
    "tooltipDelayMs": 8000,
    "mountDelayMs": 3000,
    "greeting": "Здравствуйте! Я цифровой консьерж {{brandName}}. Чем могу помочь?",
}

DEFAULT_SECTION_ORDER = [
    "hero",
    "promotion",
    "services",
    "gallery",
    "team",
    "reviews",
    "about",
    "faq",
    "bookingContacts",
]
