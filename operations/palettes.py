"""Curated light palettes. Only these validated values are rendered into CSS."""

COLORS = {
    "green": ("Forest", "#22863a"),
    "emerald": ("Emerald", "#047857"),
    "teal": ("Teal", "#0f766e"),
    "cyan": ("Ocean", "#0e7490"),
    "sky": ("Sky", "#0369a1"),
    "blue": ("Blue", "#1d4ed8"),
    "indigo": ("Indigo", "#4338ca"),
    "violet": ("Violet", "#6d28d9"),
    "purple": ("Purple", "#7e22ce"),
    "fuchsia": ("Orchid", "#a21caf"),
    "pink": ("Pink", "#be185d"),
    "rose": ("Rose", "#be123c"),
    "red": ("Ruby", "#b91c1c"),
    "orange": ("Terracotta", "#c2410c"),
    "amber": ("Amber", "#a16207"),
    "slate": ("Slate", "#475569"),
}
COLOR_CHOICES = [(key, value[0]) for key, value in COLORS.items()]


def palette(key):
    key = key if key in COLORS else "green"
    name, primary = COLORS[key]
    rgb = tuple(int(primary[i : i + 2], 16) for i in (1, 3, 5))

    def mix(amount):
        return "#" + "".join(f"{round(c + (255-c)*amount):02x}" for c in rgb)

    return {
        "key": key,
        "name": name,
        "primary": primary,
        "rgb": ", ".join(map(str, rgb)),
        "dark": "#" + "".join(f"{round(c*.8):02x}" for c in rgb),
        "light": mix(0.2),
        "background": mix(0.96),
    }


# Distinct companion hues for financial series on light backgrounds.
CHART_COMPANIONS = {
    "green": ("#b45309", "#1d4ed8", "#7e22ce"),
    "emerald": ("#c2410c", "#4338ca", "#be185d"),
    "teal": ("#b45309", "#6d28d9", "#be123c"),
    "cyan": ("#c2410c", "#7e22ce", "#047857"),
    "sky": ("#b45309", "#be185d", "#047857"),
    "blue": ("#c2410c", "#047857", "#a21caf"),
    "indigo": ("#b45309", "#0f766e", "#be123c"),
    "violet": ("#0f766e", "#b45309", "#be123c"),
    "purple": ("#047857", "#c2410c", "#0369a1"),
    "fuchsia": ("#0f766e", "#b45309", "#1d4ed8"),
    "pink": ("#0369a1", "#047857", "#b45309"),
    "rose": ("#0f766e", "#4338ca", "#a16207"),
    "red": ("#0369a1", "#047857", "#7e22ce"),
    "orange": ("#1d4ed8", "#047857", "#a21caf"),
    "amber": ("#4338ca", "#0f766e", "#be123c"),
    "slate": ("#0369a1", "#b45309", "#047857"),
}


def chart_palette(key):
    key = key if key in COLORS else "green"
    return {
        "series": [COLORS[key][1], *CHART_COMPANIONS[key]],
        "neutral": "#94a3b8",
        "grid": "#e2e8f0",
        "text": "#64748b",
    }
