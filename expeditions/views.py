"""Private seller workspace for paid orders and explicitly separated simulations."""
import re

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from facturation.delivery import build_delivery_slip
from paiement.models import Order
from retours.access import returns_required
from .forms import FORM_CLASSES
from .priorities import waiting_time
from .selectors import summary
from .services import change_state, FulfillmentError, RevisionConflict


STATES = [('preparing', 'À préparer'), ('ready', 'Prêtes'), ('shipped', 'Expédiées')]


def _private(response):
    response['Cache-Control'] = 'private, no-store, max-age=0'
    # Keep same-origin form POSTs compatible with CSRF; external links still
    # receive no referrer, including the private preparation reference.
    response['Referrer-Policy'] = 'same-origin'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _mail_mode():
    capture = bool(getattr(settings, 'EMAIL_TEST_MODE', False) or
                   getattr(settings, 'EMAIL_BACKEND', '') == 'courriels.backend.LocalMailboxBackend')
    return {'shipment_email_capture': capture,
            'shipment_mail_delivery_enabled': bool(getattr(settings, 'EMAIL_DELIVERY_ENABLED', False))}


def _form_error(form, exc):
    if isinstance(exc, ValidationError):
        for message in exc.messages:
            form.add_error(None, message)
    else:
        form.add_error(None, str(exc))


def _forms(order, info, action='', data=None):
    initial = {'revision': info['revision'], 'carrier': info.get('carrier') or 'colissimo',
               'carrier_name': info.get('carrier_name', ''),
               'tracking_number': info.get('tracking_number', '')}
    return {key: cls(data if key == action else None, initial=initial.copy(), is_demo=order.is_demo,
                     auto_id='id_' + key + '_%s')
            for key, cls in FORM_CLASSES.items()}


@returns_required
@require_GET
def overview(request):
    scope = 'demo' if settings.DEBUG and request.GET.get('scope') == 'demo' else 'paid'
    state = request.GET.get('state', 'preparing')
    if state not in dict(STATES) and state != 'all':
        state = 'preparing'
    query = request.GET.get('q', '').strip()[:100]
    # A checkout created earlier but paid later must not jump ahead of an
    # already paid purchase. Simulations and legacy rows may have no paid_at.
    orders = Order.objects.filter(status=scope).select_related('fulfillment').prefetch_related('returns').annotate(
        fulfillment_priority_at=Coalesce('paid_at', 'created_at'))
    preparing = Q(fulfillment__isnull=True) | Q(fulfillment__state='preparing')
    counts = orders.aggregate(preparing=Count('pk', filter=preparing),
        ready=Count('pk', filter=Q(fulfillment__state='ready')),
        shipped=Count('pk', filter=Q(fulfillment__state='shipped')))
    if state == 'preparing':
        orders = orders.filter(preparing)
    elif state != 'all':
        orders = orders.filter(fulfillment__state=state)
    if state in ('preparing', 'ready'):
        orders = orders.order_by('fulfillment_priority_at', 'created_at', 'pk')
        ordering_label = 'Les commandes les plus anciennes sont affichées en premier.'
    elif state == 'shipped':
        orders = orders.annotate(fulfillment_departure_at=Coalesce(
            'fulfillment__shipped_at', 'paid_at', 'created_at')).order_by(
                '-fulfillment_departure_at', '-created_at', '-pk')
        ordering_label = 'Les derniers départs sont affichés en premier.'
    else:
        orders = orders.order_by('-fulfillment_priority_at', '-created_at', '-pk')
        ordering_label = 'Les commandes les plus récentes sont affichées en premier.'
    if query:
        # Customer names, addresses and emails are encrypted; search only the reference.
        orders = orders.filter(id__icontains=query) if re.fullmatch(r'[0-9a-fA-F-]+', query) else orders.none()
    page = Paginator(orders, 25).get_page(request.GET.get('page'))
    rows = []
    now = timezone.now()
    for order in page:
        shipping = order.shipping_details if isinstance(order.shipping_details, dict) else {}
        shipment = summary(order)
        since = order.paid_at or order.created_at
        waiting = None if shipment['state'] == 'shipped' else {
            'since': since, 'label': 'En attente d’expédition depuis ' + waiting_time(since, now)}
        rows.append({'order': order, 'shipment': shipment, 'waiting': waiting,
                     'customer_name': str(shipping.get('name') or ''),
                     'quantity': sum(item['quantity'] for item in order.items
                         if isinstance(item, dict) and type(item.get('quantity')) is int and item['quantity'] > 0)
                         if isinstance(order.items, list) else 0})
    tabs = [{'key': key, 'label': label, 'count': counts[key]} for key, label in STATES]
    return _private(render(request, 'expeditions/overview.html', {
        'rows': rows, 'page_obj': page, 'scope': scope, 'state': state,
        'state_tabs': tabs, 'query': query, 'allow_demo': bool(settings.DEBUG), **_mail_mode(),
        'ordering_label': ordering_label,
    }))


@returns_required
@require_http_methods(['GET', 'POST'])
def detail(request, pk):
    statuses = ('paid', 'demo') if settings.DEBUG else ('paid',)
    order = get_object_or_404(Order.objects.select_related('fulfillment', 'invoice').prefetch_related('returns'), pk=pk, status__in=statuses)
    info = summary(order)
    action = request.POST.get('action', '') if request.method == 'POST' else ''
    forms = _forms(order, info, action=action, data=request.POST if request.method == 'POST' else None)
    status = 200
    if request.method == 'POST':
        if action not in forms:
            messages.error(request, 'Cette action n’est pas reconnue. Rechargez la fiche de la commande.')
            status = 400
        else:
            form = forms[action]
            if form.is_valid():
                data = form.cleaned_data
                try:
                    change_state(order.pk, actor=request.user,
                        expected_revision=data['revision'], action=action,
                        carrier=data.get('carrier', 'colissimo'),
                        carrier_name=data.get('carrier_name', ''),
                        tracking_number=data.get('tracking_number', ''),
                        notify_customer=(True if action in ('ship', 'update_tracking', 'resend_notice') else data.get('notify_customer', False)),
                        note=data.get('note', ''))
                except RevisionConflict as exc:
                    _form_error(form, exc)
                    status = 409
                except (FulfillmentError, ValidationError) as exc:
                    _form_error(form, exc)
                else:
                    feedback = {
                        'ready': 'La commande est marquée prête. Elle n’est pas encore expédiée.',
                        'reopen': 'La commande est à nouveau en préparation.',
                        'ship': ('L’expédition est simulée.' if order.is_demo else 'L’expédition et le numéro de suivi sont enregistrés.'),
                        'update_tracking': 'La correction du suivi est enregistrée dans l’historique.',
                        'resend_notice': 'La demande de renvoi du message est enregistrée.',
                    }[action]
                    if data.get('notify_customer') or action in ('ship', 'update_tracking', 'resend_notice'):
                        feedback += ' Consultez le statut du message ci-dessous : sa réception par le client n’est pas présumée.'
                    messages.success(request, feedback)
                    return _private(redirect('expeditions:detail', pk=order.pk))
    packing = build_delivery_slip(order)
    return _private(render(request, 'expeditions/detail.html', {
        'order': order, 'shipment': info, 'packing': packing,
        **{key + '_form': form for key, form in forms.items()},
        'submitted_action': action, 'submitted_form': forms.get(action), **_mail_mode(),
    }, status=status))
