"""Session selection is only a hint; every price and recipient is revalidated."""
from decimal import Decimal

from avoirs.models import CustomerBenefit
from avoirs.services import BenefitError, quote

SESSION_KEY = 'checkout_customer_benefit'


def summary_context(request, subtotal, benefit, quoted, error):
    """The cart and checkout show the same server-validated article discount."""
    discount = Decimal(quoted['discount_amount']) / 100 if quoted else Decimal('0.00')
    return {
        'benefit': benefit, 'benefit_selected': request.session.get(SESSION_KEY) is not None,
        'benefit_error': error, 'discount': discount, 'discounted_total': subtotal - discount,
    }


def validate_discounted_total(subtotal, discount, shipping_cents=None):
    from .shipping import free_shipping_threshold
    # EUR card payments require at least 50 cents. Below the free-shipping
    # threshold delivery is still unknown, so do not assume its charge is zero.
    remaining = subtotal - discount + shipping_cents if shipping_cents is not None else subtotal - discount
    from boutique.shipping import configuration as delivery_configuration
    known = shipping_cents is not None or (not delivery_configuration()['managed']
                                          and subtotal >= int(free_shipping_threshold() * 100))
    if known and 0 < remaining < 50:
        raise BenefitError('Le total laisserait moins de 0,50 € à payer, un montant trop faible pour le paiement par carte. Ajoutez un article ou contactez la boutique.')


def selection(request, subtotal, order=None):
    identifier = request.session.get(SESSION_KEY)
    if identifier is None:
        return None, None, ''
    try:
        benefit = CustomerBenefit.objects.get(pk=identifier)
    except (CustomerBenefit.DoesNotExist, ValueError, TypeError):
        return None, None, 'Ce code n’est plus disponible. Retirez-le pour poursuivre.'
    try:
        quoted = quote(benefit, request.user, subtotal, order=order)
        from .shipping import request_shipping_cost
        validate_discounted_total(subtotal, quoted['discount_amount'], request_shipping_cost(request, subtotal, order))
    except BenefitError as error:
        return benefit, None, str(error)
    return benefit, quoted, ''


def displayed_quote(benefit, quoted):
    if benefit is None or quoted is None:
        return None
    return {'benefit': benefit.pk, 'revision': quoted['snapshot']['benefit_revision'],
            'discount_amount': quoted['discount_amount']}


def redemption_identity(order):
    if order is None:
        return None
    from avoirs.models import BenefitRedemption
    return BenefitRedemption.objects.filter(order=order, state='held').values_list('benefit_id', flat=True).first()
