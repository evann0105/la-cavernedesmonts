"""Shared read-only cancellation checks, independent of the refund outcome."""
from django.core.exceptions import ObjectDoesNotExist


def cancellation_for(order):
    try:
        return order.cancellation
    except ObjectDoesNotExist:
        return None


def is_cancelled(order):
    if cancellation_for(order) is not None:
        return True
    try:
        return order.fulfillment.state == 'cancelled'
    except ObjectDoesNotExist:
        return False
