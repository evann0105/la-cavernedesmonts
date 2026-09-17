from django.contrib import admin
from .models import CreditNote, Voucher

@admin.register(CreditNote)
class CreditAdmin(admin.ModelAdmin):
    list_display=('reference','order','amount','issued_at')
    search_fields=('reference','order__customer_email')

@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display=('code','user','active','expires_at')
    search_fields=('code','user__username')
    list_filter=('active',)
