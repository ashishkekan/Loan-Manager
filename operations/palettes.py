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
    rgb = tuple(int(primary[i:i+2], 16) for i in (1, 3, 5))
    def mix(amount):
        return "#" + "".join(f"{round(c + (255-c)*amount):02x}" for c in rgb)
    return {"key": key, "name": name, "primary": primary,
            "rgb": ", ".join(map(str, rgb)),
            "dark": "#" + "".join(f"{round(c*.8):02x}" for c in rgb),
            "light": mix(.2), "background": mix(.96)}
