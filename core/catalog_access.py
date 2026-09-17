from functools import wraps
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache
from two_factor.utils import default_device


def can_manage_catalog(user):
    return user.is_authenticated and user.is_active and user.is_staff and user.has_perm('core.manage_catalog')


def catalog_required(view):
    @never_cache
    @wraps(view)
    def protected(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not can_manage_catalog(request.user):
            raise PermissionDenied
        if not request.user.is_verified():
            if not default_device(request.user):
                return redirect('two_factor:setup')
            return redirect_to_login(request.get_full_path())
        return view(request, *args, **kwargs)
    return protected
