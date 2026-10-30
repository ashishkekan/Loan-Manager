from django.contrib import admin

from .models import AuditEvent, Branch, Membership, Organization


@admin.register(AuditEvent)
class AuditAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "model", "object_id", "action")
    readonly_fields = (
        "actor",
        "model",
        "object_id",
        "action",
        "before",
        "after",
        "reason",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser


admin.site.register(Organization)
admin.site.register(Branch)
admin.site.register(Membership)
