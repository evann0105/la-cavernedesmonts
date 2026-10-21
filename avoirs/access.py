"""Recipient access, shared by account pages and checkout validation."""
from django.db.models import Q
from django.utils.crypto import constant_time_compare

from config.encrypted_fields import email_digest
from connexion.email_verification import email_verified, local_email_bypass
from .models import CustomerBenefit


def email_confirmation_required():
    return not local_email_bypass()


def benefits_access_allowed(user):
    return bool(user and user.is_authenticated and user.is_active
                and (local_email_bypass() or email_verified(user)))


def customer_matches(benefit, user):
    if not benefits_access_allowed(user):
        return False
    if not constant_time_compare(benefit.recipient_email_hash, email_digest(user.email)):
        return False
    if email_verified(user):
        # A genuinely verified recipient may claim an as-yet unbound grant.
        return (benefit.bound_user_id in (None, user.pk)
                and benefit.user_id in (None, user.pk))
    # Local trials do not prove ownership of an email address. They can only
    # use the account assignment explicitly recorded by the seller.
    return benefit.user_id == user.pk and benefit.bound_user_id == user.pk


def benefits_for(user):
    if not benefits_access_allowed(user):
        return CustomerBenefit.objects.none()
    benefits = CustomerBenefit.objects.filter(recipient_email_hash=email_digest(user.email))
    if email_verified(user):
        return benefits.filter(Q(bound_user_id=user.pk) | Q(bound_user_id__isnull=True),
                               Q(user_id=user.pk) | Q(user_id__isnull=True))
    return benefits.filter(user_id=user.pk, bound_user_id=user.pk)
