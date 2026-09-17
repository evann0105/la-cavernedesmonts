from two_factor.admin import AdminSiteOTPRequired
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse
from two_factor.utils import default_device


class BoutiqueAdminSite(AdminSiteOTPRequired):
    site_header = 'La Caverne des Monts'

    def login(self, request, extra_context=None):
        if request.user.is_authenticated and request.user.is_staff:
            if not default_device(request.user):
                return redirect('two_factor:setup')
        return redirect_to_login(reverse('admin:index'), login_url='two_factor:login')
