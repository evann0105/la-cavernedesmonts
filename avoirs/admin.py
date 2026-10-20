"""Financial history is read-only here; seller actions use the dedicated page."""
from django.contrib import admin
from .models import BenefitEvent, BenefitMail, BenefitRedemption, CustomerBenefit


class ReadOnlyBenefitAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CustomerBenefit)
class CustomerBenefitAdmin(ReadOnlyBenefitAdmin):
    list_display = ['code', 'label', 'kind', 'value', 'active', 'created_at', 'notified_at']
    list_filter = ['kind', 'active']
    search_fields = ['code', 'label']


@admin.register(BenefitRedemption)
class RedemptionAdmin(ReadOnlyBenefitAdmin):
    list_display = ['benefit', 'order', 'state', 'discount_amount', 'created_at', 'used_at']
    list_filter = ['state']
    search_fields = ['benefit__code']


@admin.register(BenefitEvent)
class BenefitEventAdmin(ReadOnlyBenefitAdmin):
    list_display = ['benefit', 'label', 'created_at']


@admin.register(BenefitMail)
class BenefitMailAdmin(ReadOnlyBenefitAdmin):
    list_display = ['benefit', 'created_at', 'sent_at', 'cancelled_at', 'attempts']
    exclude = ['payload']

    def get_readonly_fields(self, request, obj=None):
        return [field for field in super().get_readonly_fields(request, obj) if field != 'payload']
