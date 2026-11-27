"""Event theme packs.

The host picks an event type in event.json ("type": "birthday", ...).
Each theme changes the dashboard colours, Uncle Gus's accessory and the
tone of his messages. Everything sits on the base Gather brand.
"""

THEMES = {
    "dinner": {
        "label": "Dinner party",
        "colors": {"primary": "#5F6F52", "accent": "#C4661F", "bg": "#FEFAE0",
                   "surface": "#FFFDF0", "soft": "#F9EBC7", "ink": "#2E3628",
                   "muted": "#4A5441", "line": "#EADFC0", "danger": "#783D19"},
        "accessory": "none",
        "tone": "warm and gracious, like a host pouring the first glass",
    },
    "birthday": {
        "label": "Birthday",
        "colors": {"primary": "#7A3E9D", "accent": "#F2B134", "bg": "#FFF8EE",
                   "surface": "#FFFFFF", "soft": "#F6E7F7", "ink": "#2B2033",
                   "muted": "#5A4D63", "line": "#EADCEF", "danger": "#A3223A"},
        "accessory": "party_hat",
        "tone": "playful and excited, a little over the top, celebrating the birthday person",
    },
    "wedding": {
        "label": "Wedding",
        "colors": {"primary": "#56695A", "accent": "#B8935A", "bg": "#FBF8F2",
                   "surface": "#FFFFFF", "soft": "#EFF0E6", "ink": "#2C2A26",
                   "muted": "#55524A", "line": "#E6E0D2", "danger": "#8C3B2E"},
        "accessory": "bow_tie",
        "tone": "elegant, heartfelt and polite, never jokey about the couple",
    },
    "holiday": {
        "label": "Holiday celebration",
        "colors": {"primary": "#2F5D50", "accent": "#B23A3A", "bg": "#F8F4EC",
                   "surface": "#FFFDF8", "soft": "#ECE6DA", "ink": "#22302B",
                   "muted": "#4A5751", "line": "#E2DACB", "danger": "#8E2C2C"},
        "accessory": "scarf",
        "tone": "cosy and festive, respectful of whichever holiday the host is celebrating",
    },
    "bbq": {
        "label": "BBQ / outdoor",
        "colors": {"primary": "#8A3B1E", "accent": "#E0A030", "bg": "#FFF7EA",
                   "surface": "#FFFFFF", "soft": "#F8E6CC", "ink": "#2E211A",
                   "muted": "#5E4A3E", "line": "#EEDCC2", "danger": "#9E2B1C"},
        "accessory": "apron",
        "tone": "relaxed and sunny, like someone flipping burgers and telling stories",
    },
}

DEFAULT = "dinner"


def get(event_type: str | None) -> dict:
    key = (event_type or DEFAULT).strip().lower()
    theme = THEMES.get(key, THEMES[DEFAULT])
    return {"key": key if key in THEMES else DEFAULT, **theme}
