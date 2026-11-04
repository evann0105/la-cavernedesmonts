"""Use the maintained trust store for every Stripe SDK request.

The SDK otherwise prefers its own bundled certificates over Requests/Certifi.
Set this once at Django startup, including workers and management commands;
the same policy covers checkout, coupons, refunds and provider reconciliation.
"""
import certifi
import stripe


def configure_stripe_tls():
    stripe.ca_bundle_path = certifi.where()
    stripe.verify_ssl_certs = True
