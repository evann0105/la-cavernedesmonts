from functools import wraps
import hashlib
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse
from django.conf import settings
from django.views.decorators.cache import never_cache
from connexion.email_verification import email_access_allowed


def order_token(order):
    return signing.dumps({'order':str(order.pk),'recipient':hashlib.sha256(order.customer_email.encode()).hexdigest()}, salt='return-order-v1')


def order_link(order):
    from urllib.parse import urlencode
    return settings.SITE_URL.rstrip('/') + reverse('retours:order',args=[order.pk]) + '?' + urlencode({'token':order_token(order)})


def owns_order(request, order):
    if request.user.is_authenticated and order.user_id == request.user.pk and email_access_allowed(request.user):
        return True
    try:
        payload = signing.loads(request.GET.get('token',''),salt='return-order-v1',max_age=400*24*3600)
        return payload == {'order':str(order.pk),'recipient':hashlib.sha256(order.customer_email.encode()).hexdigest()}
    except signing.BadSignature:
        return False


def manager(user):
    return user.is_authenticated and user.is_active and user.is_staff and user.has_perm('retours.manage_returns')


def returns_required(view):
    @never_cache
    @wraps(view)
    def protected(request,*args,**kwargs):
        if not request.user.is_authenticated:return redirect_to_login(request.get_full_path())
        if not manager(request.user):raise PermissionDenied
        if not email_access_allowed(request.user):return redirect('connexion:verify_email')
        return view(request,*args,**kwargs)
    return protected
