import logging
from datetime import timedelta
from django.conf import settings
from boutique.configuration import configuration
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from courriels.messages import build_message
from .models import ReturnMail
from .access import order_link

logger = logging.getLogger(__name__)


def enqueue(order, key, kind, details, request=None, withdrawal=False, recipient=None, url=None):
    ReturnMail.objects.get_or_create(key=key, defaults={'order':order,'request':request,'payload':{'kind':kind,'details':details,'withdrawal':withdrawal,'recipient':recipient,'url':url}})


def order_confirmation(order):
    if not order.customer_email:return
    details = f'Commande {order.pk}\n'
    for index,item in enumerate(order.items):
        gross = item['unit_amount'] * item['quantity']
        details += f"{item['quantity']} × {item['name']} — {item.get('size','')} {item.get('color','')}\n"
        details += f"Prix unitaire avant remise : {item['unit_amount']/100:.2f} € TTC\n"
        if order.paid_line_totals is not None and len(order.paid_line_totals) == len(order.items):
            paid = order.paid_line_totals[index]
            details += f'Remise sur cette ligne : {(gross-paid)/100:.2f} € · Total payé pour cette ligne : {paid/100:.2f} €\n'
        else:
            details += f'Total de cette ligne avant remise : {gross/100:.2f} €\n'
    details += f'\nTotal payé : {order.display_total:.2f} €'
    if order.return_shipping_amount is not None:
        details += f'\nDont livraison : {order.return_shipping_amount/100:.2f} €'
    if order.shipping_details:
        shipping=order.shipping_details;address=shipping.get('address') or {}
        details += '\nLivraison : '+str(shipping.get('name') or '')+' · '+', '.join(str(address.get(k) or '') for k in ('line1','line2','postal_code','city','country') if address.get(k))
    details += '\nAchat ponctuel, sans abonnement ni renouvellement automatique.'
    if order.shipping_snapshot:
        from paiement.shipping import snapshot_delivery_text
        details += '\n' + snapshot_delivery_text(order.shipping_snapshot)
    enqueue(order,f'order-{order.pk}','order',details,withdrawal=True)


def return_notice(record, kind, label):
    order = record.order
    details = f'Dossier {record.short_reference} · Commande {order.pk}\n{label}\n'
    details += '\n'.join(f'{i.quantity} × {i.name} — {i.size} {i.color}' for i in record.lines.all())
    details += f'\nRetour des articles : {record.get_return_state_display()}\nRemboursement : {record.get_refund_state_display()}\n'
    from .amounts import email_breakdown
    details += '\n' + email_breakdown(record) + '\n'
    if kind == 'return_completed':
        details += '\nQuantités finales par article :\n' + '\n'.join(f'{i.name} — {i.size} {i.color} : {i.received} reçu(s), {i.restocked} remis en stock' for i in record.lines.all())
    if kind == 'return_requested':
        name = (record.declarant or {}).get('name', '')
        if name:
            details += 'Déclarant : ' + name + '\n'
        details += ('Déclaration reçue le '+timezone.localtime(record.created_at).strftime('%d/%m/%Y à %H:%M:%S %Z')+'.\nNature : '+record.get_kind_display()+'\nPrécision : '+(record.reason or 'Aucune')+'\nVotre déclaration de rétractation / demande de retour a été enregistrée. Elle ne signifie pas que le remboursement a déjà été effectué.\n' +
                    ('Pour une rétractation, renvoyez les articles dans les 14 jours suivant votre déclaration.\n' if record.kind=='withdrawal' else 'Pour un défaut ou une erreur, contactez la boutique avant le renvoi pour organiser sa prise en charge.\n') + 'Adresse de retour :\n' + configuration()['return_address'] +
                    '\nJoignez la référence du dossier au colis. Conservez votre preuve d’expédition.\n' + settings.RETURN_COSTS_TEXT)
    enqueue(order,f'{kind}-{record.pk}',kind,details,request=record,
            recipient=order.customer_email if kind == 'return_requested' else None)


def deliver_pending(limit=30, order_id=None):
    if not settings.EMAIL_DELIVERY_ENABLED:return 0
    sent = 0
    cutoff = timezone.now()-timedelta(minutes=5)
    queued = ReturnMail.objects.filter(sent_at__isnull=True)
    if order_id is not None:queued=queued.filter(order_id=order_id)
    ids = queued.filter(Q(last_attempt_at__isnull=True) | Q(last_attempt_at__lt=cutoff)).order_by('pk').values_list('pk',flat=True)[:limit]
    for pk in list(ids):
        try:
            with transaction.atomic():
                mail = ReturnMail.objects.select_for_update().select_related('order').get(pk=pk)
                if mail.sent_at:continue
                payload = mail.payload
                # A queued acknowledgement must never be redirected after a
                # change of email. Keep it pending for explicit review.
                if (payload['kind'] == 'return_requested' and payload.get('recipient') is not None
                        and payload['recipient'] != mail.order.customer_email):
                    logger.warning('Withdrawal acknowledgement recipient requires review (mail %s).', pk)
                    continue
                mail.attempts += 1;mail.last_attempt_at = timezone.now()
                mail.save(update_fields=['attempts','last_attempt_at'])
                url = payload.get('url') or order_link(mail.order)
                message = build_message(payload['kind'],payload.get('recipient') or mail.order.customer_email,url,payload['details'],test=mail.order.is_demo,withdrawal_url=url if payload.get('withdrawal') else '')
                if payload['kind'] == 'order' and mail.order.terms_snapshot.get('text'):
                    message.attach('conditions-vente-et-retractation.txt',mail.order.terms_snapshot['text'],'text/plain; charset=utf-8')
                try:
                    if not message.send():continue
                except Exception:
                    logger.warning('Transactional email delivery failed (mail %s).',pk)
                    continue
                mail.sent_at = timezone.now();mail.save(update_fields=['sent_at']);sent += 1
        except ReturnMail.DoesNotExist:continue
    return sent
