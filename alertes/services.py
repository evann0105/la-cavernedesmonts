"""Generate the seller's paid-order notice only at the confirmed payment transition."""
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from paiement.models import Order
from .models import AlertNotice, AlertSettings


def get_settings():
    """Read defaults without writing or enabling outgoing notifications."""
    return AlertSettings.objects.filter(pk=1).first() or AlertSettings(pk=1)


@transaction.atomic
def enqueue_paid_order(order):
    """The payment path calls this before commit; a repeated event is harmless."""
    order = Order.objects.select_for_update().filter(pk=getattr(order, 'pk', order)).first()
    if order is None or order.status != 'paid' or order.paid_at is None or order.total_paid is None:
        return None
    preferences = get_settings()
    recipient = preferences.recipient.strip().lower()
    if not preferences.order_notifications_enabled or not recipient:
        return None
    reference = str(order.pk)[:8].upper()
    details = (f'Commande {reference}\n'
               f'Paiement confirmé le {timezone.localtime(order.paid_at):%d/%m/%Y à %H:%M}.\n'
               f'Montant payé : {order.display_total:.2f} €\n\n')
    # Only purchase contents travel in this alert; addresses and private links
    # remain in the authenticated order workspace.
    for item in order.items:
        details += f"{item.get('quantity', 0)} × {item.get('name', 'Article')}"
        variant = ' · '.join(str(item.get(key) or '') for key in ('size', 'color') if item.get(key))
        if variant:
            details += ' — ' + variant
        details += '\n'
    payload = {
        'recipient': recipient, 'settings_revision': preferences.revision,
        'subject': f'Nouvelle commande payée · {reference}',
        'title': 'Une commande est à préparer.',
        'intro': 'Le paiement a été confirmé. Retrouvez la commande dans votre espace de gestion.',
        'details': details, 'action': 'Préparer la commande',
        'url': reverse('expeditions:detail', args=[order.pk]),
    }
    return AlertNotice.objects.get_or_create(key=f'paid-order-{order.pk}',
        defaults={'kind': 'paid_order', 'order': order, 'payload': payload})[0]
