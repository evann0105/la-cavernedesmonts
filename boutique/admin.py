from django.contrib import admin
from django.shortcuts import redirect
from core.catalog_access import can_manage_catalog
from .models import OpeningHours


@admin.register(OpeningHours)
class OpeningHoursAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return can_manage_catalog(request.user)

    def has_view_permission(self, request, obj=None):
        return can_manage_catalog(request.user)

    def has_change_permission(self, request, obj=None):
        return can_manage_catalog(request.user)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        return redirect('boutique:hours')

    def change_view(self, request, object_id, form_url='', extra_context=None):
        return redirect('boutique:hours')
