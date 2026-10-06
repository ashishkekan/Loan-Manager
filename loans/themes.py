from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from .models import AppearancePreference, SiteAppearance

PALETTES = {
    "green": ("Green", "#087566", "modern"),
    "pink": ("Pink", "#b62c70", "rounded"),
    "black": ("Black", "#27272a", "system"),
    "white": ("White", "#526174", "modern"),
    "red": ("Red", "#bd303d", "modern"),
    "orange": ("Orange", "#ac4b0b", "rounded"),
    "blue": ("Blue", "#245dc1", "modern"),
    "purple": ("Purple", "#7042b5", "classic"),
}
FONTS = {"auto", "modern", "rounded", "classic", "system"}


def theme_context(request):
    site = SiteAppearance.objects.filter(pk=1).first() or SiteAppearance()
    pref = (AppearancePreference.objects.filter(user=request.user).first()
            if request.user.is_authenticated else None)
    editable = site.allow_personal_themes or request.user.is_staff
    personal = bool(pref and pref.color_palette in PALETTES and editable)
    palette = pref.color_palette if personal else site.default_palette
    if palette not in PALETTES:
        palette = "green"
    font = pref.font_style if personal else site.default_font
    mode = pref.theme if personal else "palette"
    effective_font = PALETTES[palette][2] if font == "auto" else font
    return {
        "appearance": {
            "palette": palette, "font": effective_font, "mode": mode,
            "theme": "dark" if mode == "dark" or (mode == "palette" and palette == "black") else "light",
            "selection": pref.color_palette if personal else "inherit",
            "fontSelection": pref.font_style if personal else "auto",
            "editable": editable, "defaultPalette": site.default_palette,
            "defaultFont": site.default_font, "allowPersonal": site.allow_personal_themes,
        },
        "theme_palettes": [{"id": key, "label": value[0], "color": value[1]} for key, value in PALETTES.items()],
    }


@login_required
@require_POST
def save_theme(request):
    scope = request.POST.get("scope", "personal")
    if scope not in {"personal", "workspace"}:
        return JsonResponse({"error": "Invalid scope."}, status=400)
    site = SiteAppearance.objects.filter(pk=1).first() or SiteAppearance(pk=1)
    if scope == "workspace" and not request.user.is_staff:
        return JsonResponse({"error": "Only admins can change workspace defaults."}, status=403)
    if scope == "personal" and not site.allow_personal_themes and not request.user.is_staff:
        return JsonResponse({"error": "Your admin manages the workspace appearance."}, status=403)
    palette = request.POST.get("palette", "")
    font = request.POST.get("font", "auto")
    mode = request.POST.get("mode", "palette")
    allowed = set(PALETTES) | ({"inherit"} if scope == "personal" else set())
    if palette not in allowed or font not in FONTS or mode not in {"light", "dark", "system", "palette"}:
        return JsonResponse({"error": "Choose a valid palette, font and mode."}, status=400)
    if scope == "workspace":
        allow = request.POST.get("allow_personal", "true")
        if allow not in {"true", "false"}:
            return JsonResponse({"error": "Invalid personal-theme policy."}, status=400)
        site.default_palette, site.default_font = palette, font
        site.allow_personal_themes = allow == "true"
        site.save()
    else:
        AppearancePreference.objects.update_or_create(user=request.user, defaults={
            "color_palette": palette, "font_style": font, "theme": mode,
        })
    return JsonResponse(theme_context(request)["appearance"])
