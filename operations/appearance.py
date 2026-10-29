from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from .models import SiteAppearance
from .palettes import COLORS, palette, chart_palette


class AppearanceForm(forms.ModelForm):
    class Meta:
        model = SiteAppearance
        fields = ["color"]
        widgets = {"color": forms.RadioSelect}


def site_appearance(request):
    appearance = SiteAppearance.objects.filter(pk=1).first()
    color = appearance.color if appearance else "green"
    return {"site_palette": palette(color), "site_chart_palette": chart_palette(color)}


@login_required
@require_http_methods(["GET", "POST"])
def appearance_view(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only an administrator can change site appearance.")
    appearance = SiteAppearance.objects.filter(pk=1).first() or SiteAppearance(pk=1)
    form = AppearanceForm(request.POST or None, instance=appearance)
    if request.method == "POST" and form.is_valid():
        appearance = form.save(commit=False)
        appearance.updated_by = request.user
        appearance.save()
        messages.success(request, "Site appearance updated for everyone.")
        return redirect("operations:appearance")
    return render(request, "operations/appearance.html", {"form": form, "appearance": appearance, "palettes": [palette(key) for key in COLORS]})
