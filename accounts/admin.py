from django.contrib import admin
from accounts.models import Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "kyc_verified", "available_funds")
    list_filter = ("role", "kyc_verified")
    search_fields = ("user__username",)
