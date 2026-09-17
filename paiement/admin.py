from django.contrib import admin
from .models import Order

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'status', 'customer_email', 'total_paid', 'created_at')
    list_filter = ('status',)
    search_fields = ('customer_email', 'stripe_session_id')
    readonly_fields = tuple(f.name for f in Order._meta.fields)
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False
