"""A destination for shop-wide alerts is managed by the seller with both roles."""
from django.core.exceptions import PermissionDenied
from core.catalog_access import can_manage_catalog
from retours.access import manager
from connexion.email_verification import email_access_allowed


def can_configure(user):
    return can_manage_catalog(user) and manager(user) and email_access_allowed(user)


def authorize_configuration(user):
    if not can_configure(user):
        raise PermissionDenied
