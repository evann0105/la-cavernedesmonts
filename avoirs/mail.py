"""Durable transactional notifications. Test mode uses the existing local backend."""
import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from courriels.messages import render_message
from config.encrypted_fields import email_digest
from .models import BenefitMail, CustomerBenefit

logger = logging.getLogger(__name__)


def conditions(benefit):
    text = f'Code personnel : {benefit.code}\n{benefit.label}\n{benefit.get_kind_display()} : {benefit.value_label}\n'
    text += 'Créé le ' + timezone.localtime(benefit.created_at).strftime('%d/%m/%Y à %H:%M %Z') + '.\n'
    if benefit.justification:
        text += f'Message de la boutique :\n{benefit.justification}\n'
    text += f'Minimum d’articles : {benefit.effective_minimum:.2f} €, hors frais de livraison.\n'
    if benefit.starts_at:
        text += 'Valable à partir du ' + timezone.localtime(benefit.starts_at).strftime('%d/%m/%Y à %H:%M %Z') + '.\n'
    if benefit.ends_at:
        text += 'Valable avant le ' + timezone.localtime(benefit.ends_at).strftime('%d/%m/%Y à %H:%M %Z') + '.\n'
    else:
        text += 'Sans date d’expiration fixée.\n'
    text += ('Ce code est lié à votre adresse e-mail. Connectez-vous au compte utilisant cette adresse et confirmez-la pour retrouver le code dans « Mes avoirs ». '
             'Saisissez-le lors de la commande, avant le paiement. Un seul code client par commande. '
             'Il est utilisable une seule fois et ne sera consommé qu’après confirmation du paiement. '
             'La remise porte sur les articles et ne réduit pas les frais de livraison.\n')
    if benefit.kind == 'credit':
        text += 'L’avoir doit être utilisé en totalité dans une seule commande : le montant des articles doit être au moins égal à celui de l’avoir.\n'
    elif benefit.kind == 'amount':
        text += 'Le bon de réduction est plafonné au montant des articles ; aucun reliquat n’est créé.\n'
    text += 'Si vous n’avez pas encore de compte, créez-le avec cette même adresse e-mail puis confirmez-la. Cet avantage ne remplace pas un remboursement légal dû.'
    return text


def enqueue(benefit, key=None, resend=False):
    revision = benefit.updated_at.isoformat()
    suffix = key or (secrets.token_urlsafe(18) if resend else revision)
    key = f'benefit-{benefit.pk}-' + hashlib.sha256(str(suffix).encode()).hexdigest()
    return BenefitMail.objects.get_or_create(key=key, defaults={
        'benefit': benefit, 'payload': {'recipient': benefit.recipient_email, 'revision': revision, 'details': conditions(benefit)},
    })[0]


def deliver_pending(limit=30, benefit_id=None):
    if not settings.EMAIL_DELIVERY_ENABLED:
        return 0
    cutoff = timezone.now() - timedelta(minutes=5)
    queued = BenefitMail.objects.filter(sent_at__isnull=True, cancelled_at__isnull=True)
    if benefit_id is not None:
        queued = queued.filter(benefit_id=benefit_id)
    identities = list(queued.filter(Q(last_attempt_at__isnull=True) | Q(last_attempt_at__lt=cutoff))
                      .order_by('pk').values('pk', 'benefit_id')[:limit])
    sent = 0
    for identity in identities:
        try:
            with transaction.atomic():
                # Same lock order as seller changes: benefit, then notification.
                benefit = CustomerBenefit.objects.select_for_update().get(pk=identity['benefit_id'])
                notice = BenefitMail.objects.select_for_update().get(pk=identity['pk'])
                if notice.sent_at or notice.cancelled_at:
                    continue
                payload = notice.payload
                if (payload.get('recipient') != benefit.recipient_email
                        or payload.get('revision') != benefit.updated_at.isoformat()
                        or (benefit.bound_user_id and not benefit.user_id)
                        or (benefit.user_id and email_digest(benefit.user.email) != benefit.recipient_email_hash)
                        or not benefit.active or benefit.status in ('used', 'expired')):
                    notice.cancelled_at = timezone.now()
                    notice.save(update_fields=['cancelled_at'])
                    continue
                notice.attempts += 1
                notice.last_attempt_at = timezone.now()
                notice.save(update_fields=['attempts', 'last_attempt_at'])
                test = bool(getattr(settings, 'EMAIL_TEST_MODE', False))
                context = {
                    'subject': 'Un avoir ou un bon vous attend', 'title': 'Une attention de votre boutique.',
                    'intro': 'La Caverne des Monts a préparé un avantage personnel pour votre prochaine commande.',
                    'action': 'Voir mes avoirs', 'url': settings.SITE_URL + reverse('espace:credits'),
                    'details': payload['details'], 'security': False, 'test': test,
                }
                text, html = render_message(context)
                message = EmailMultiAlternatives(
                    ('[TEST] ' if test else '') + context['subject'] + ' — La Caverne des Monts',
                    text, settings.DEFAULT_FROM_EMAIL, [payload['recipient']],
                )
                message.attach_alternative(html, 'text/html')
                try:
                    if message.send() != 1:
                        continue
                except Exception:
                    logger.warning('Customer benefit notification delivery failed (notice %s).', notice.pk)
                    continue
                notice.sent_at = timezone.now()
                notice.save(update_fields=['sent_at'])
                CustomerBenefit.objects.filter(pk=benefit.pk).update(notified_at=notice.sent_at)
                sent += 1
        except (BenefitMail.DoesNotExist, CustomerBenefit.DoesNotExist):
            continue
    return sent
