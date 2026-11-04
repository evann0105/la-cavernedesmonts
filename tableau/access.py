"""Entry points shared by catalogue and customer-service managers."""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache

from connexion.email_verification import email_access_allowed
from core.catalog_access import can_manage_catalog
from retours.access import manager


def can_access_seller_home(user):
    """Entering the workspace does not grant access to its individual tools."""
    return can_manage_catalog(user) or manager(user)


def seller_required(view):
    @never_cache
    @wraps(view)
    def protected(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not can_access_seller_home(request.user):
            raise PermissionDenied
        if not email_access_allowed(request.user):
            return redirect('connexion:verify_email')
        return view(request, *args, **kwargs)
    return protected
