from django.contrib.admin import AdminSite
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse
from .email_verification import email_access_allowed


class BoutiqueAdminSite(AdminSite):
    site_header = 'La Caverne des Monts'

    def has_permission(self, request):
        return super().has_permission(request) and email_access_allowed(request.user)

    def login(self, request, extra_context=None):
        if request.user.is_authenticated and request.user.is_staff and not email_access_allowed(request.user):
            return redirect('connexion:verify_email')
        return redirect_to_login(reverse('admin:index'), login_url='connexion:login')
