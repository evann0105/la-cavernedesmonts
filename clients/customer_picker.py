"""Private, read-only customer lookup for the seller's new SAV request form."""
import re

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from paiement.models import Order
from retours.access import returns_required
from .selectors import find_customers, get_customer


RESULT_LIMIT = 10
QUERY_LIMIT = 150


def customer_payload(customer, include_orders=False):
    """Only contact/choice data needed by the form; no private history or codes."""
    values = {
        'key': customer.key,
        'name': customer.name,
        'email': customer.user.email if customer.user_id else customer.email,
        'phone': customer.phone,
        'email_locked': bool(customer.user_id),
        'account_label': customer.account_label,
        'order_count': customer.order_count,
    }
    if include_orders:
        orders = Order.objects.filter(pk__in=customer.order_ids).order_by('-created_at', '-pk')
        values['orders'] = [
            {'value': str(order.pk),
             'label': f'{str(order.pk)[:8].upper()} · {timezone.localtime(order.created_at):%d/%m/%Y}'
                      f' · {order.display_total:.2f} €' + (' · SIMULATION' if order.is_demo else '')}
            for order in orders
        ]
    return values


def picker_results(query):
    query = ' '.join(str(query or '').split())[:QUERY_LIMIT]
    if len(query) < 2:
        return [], False
    if re.fullmatch(r'[0-9+(). /-]+', query):
        digits = ''.join(char for char in query if char.isdigit())
        if len(digits) < 4:
            return [], False
        # Search phone terms collected from both current and historical records;
        # this does not merge people sharing a phone number.
        def matches_phone(customer):
            terms = (customer.phone, *customer._terms)
            return any(re.fullmatch(r'[0-9+(). /-]+', term or '') and
                       digits in ''.join(char for char in term if char.isdigit()) for term in terms)
        rows = [customer for customer in find_customers('') if matches_phone(customer)]
    else:
        rows = find_customers(query)
    return [customer_payload(customer) for customer in rows[:RESULT_LIMIT]], len(rows) > RESULT_LIMIT


def picker_context(customer=None, query='', results=None, more=False):
    return {
        'selected_client': customer.key if customer else '',
        'picker_query': str(query or '')[:QUERY_LIMIT],
        'picker_results': results if results is not None else [],
        'picker_more': more,
        'picker_initial': customer_payload(customer, include_orders=True) if customer else None,
    }


def _private_json(values):
    response = JsonResponse(values)
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'same-origin'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@returns_required
@require_POST
def customer_search(request):
    # POST keeps names, e-mails and phone searches out of URL history/logs.
    results, more = picker_results(request.POST.get('q', ''))
    return _private_json({'results': results, 'more': more})


@returns_required
@require_POST
def customer_pick(request):
    customer = get_customer(request.POST.get('client', ''))
    return _private_json({'customer': customer_payload(customer, include_orders=True)})
