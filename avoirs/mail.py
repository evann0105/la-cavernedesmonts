"""Durable transactional notifications. Test mode uses the existing local backend."""
import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from courriels.messages import render_message
from config.encrypted_fields import email_digest
from .access import email_confirmation_required
from .models import BenefitMail, CustomerBenefit

logger = logging.getLogger(__name__)


def local_capture_enabled():
    return bool(getattr(settings, 'EMAIL_TEST_MODE', False) or
                settings.EMAIL_BACKEND == 'courriels.backend.LocalMailboxBackend')


def notification_state(benefit):
    """Describe recorded processing, without claiming delivery to an inbox.

    A previous local capture stays a local capture after SMTP is configured.
    Notifications predating delivery_mode are deliberately reported as unknown.
    """
    pending = benefit.notifications.filter(sent_at__isnull=True, cancelled_at__isnull=True).exists()
    last = benefit.notifications.filter(sent_at__isnull=False).order_by('-sent_at', '-pk').first()
    processed_at = benefit.notified_at
    mode = 'unknown'
    if last and last.payload.get('revision') == benefit.updated_at.isoformat():
        processed_at = last.sent_at
        recorded = last.payload.get('delivery_mode')
        if recorded in ('local_test', 'provider'):
            mode = recorded
    return {'pending': pending, 'processed_at': processed_at, 'mode': mode}


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
    if email_confirmation_required():
        text += 'Ce code est lié à votre adresse e-mail. Connectez-vous au compte utilisant cette adresse et confirmez-la pour retrouver le code dans « Mes avoirs ». '
    else:
        text += 'Connectez-vous au compte client auquel la boutique a attribué ce code pour le retrouver dans « Mes avoirs ». Aucune confirmation par e-mail n’est demandée pendant les essais locaux. '
    text += ('Saisissez-le dans le panier ou lors de la commande, avant le paiement. Un seul code client par commande. '
             'Il est utilisable une seule fois et ne sera consommé qu’après confirmation du paiement. '
             'La remise porte sur les articles et ne réduit pas les frais de livraison.\n')
    if benefit.kind == 'credit':
        text += 'L’avoir doit être utilisé en totalité dans une seule commande : le montant des articles doit être au moins égal à celui de l’avoir.\n'
    elif benefit.kind == 'amount':
        text += 'Le bon de réduction est plafonné au montant des articles ; aucun reliquat n’est créé.\n'
    if email_confirmation_required():
        text += 'Si vous n’avez pas encore de compte, créez-le avec cette même adresse e-mail puis confirmez-la. '
    elif not benefit.user_id:
        text += 'Si vous n’avez pas encore de compte, créez-le avec cette même adresse e-mail puis demandez à la boutique de lui attribuer le code. '
    text += 'Cet avantage ne remplace pas un remboursement légal dû.'
    return text


def enqueue(benefit, key=None, resend=False):
    revision = benefit.updated_at.isoformat()
    suffix = key or (secrets.token_urlsafe(18) if resend else revision)
    key = f'benefit-{benefit.pk}-' + hashlib.sha256(str(suffix).encode()).hexdigest()
    return BenefitMail.objects.get_or_create(key=key, defaults={
        'benefit': benefit, 'payload': {'recipient': benefit.recipient_email, 'revision': revision, 'details': conditions(benefit)},
    })[0]


def blocked_reason(notice):
    """Read the reason a queued code cannot safely be sent; never reveal details."""
    benefit, payload = notice.benefit, notice.payload
    if not isinstance(payload, dict) or not isinstance(payload.get('details'), str):
        return 'Le contenu de cet ancien message doit être vérifié depuis l’avoir.'
    if (payload.get('recipient') != benefit.recipient_email
            or payload.get('revision') != benefit.updated_at.isoformat()
            or (benefit.bound_user_id and not benefit.user_id)
            or (benefit.user_id and (benefit.bound_user_id not in (None, benefit.user_id)
                or email_digest(benefit.user.email) != benefit.recipient_email_hash))):
        return 'L’avoir ou son destinataire a changé. Préparez un nouveau message depuis sa fiche.'
    if not benefit.active or benefit.status in ('used', 'expired'):
        return 'Cet avoir est désactivé, expiré ou déjà utilisé ; ce message n’est plus à envoyer.'
    try:
        validate_email(payload['recipient'])
    except (ValidationError, TypeError):
        return 'L’adresse e-mail du destinataire est absente ou invalide.'
    return ''


def deliver_pending(limit=30, benefit_id=None, *, notice_id=None, force=False, expected_attempts=None):
    if not settings.EMAIL_DELIVERY_ENABLED or limit <= 0:
        return 0
    if force and notice_id is None:
        raise ValueError('A manual retry must target one notification.')
    cutoff = timezone.now() - timedelta(minutes=5)
    queued = BenefitMail.objects.filter(sent_at__isnull=True, cancelled_at__isnull=True)
    if benefit_id is not None:
        queued = queued.filter(benefit_id=benefit_id)
    if notice_id is not None:
        queued = queued.filter(pk=notice_id)
    if not force:
        queued = queued.filter(Q(last_attempt_at__isnull=True) | Q(last_attempt_at__lt=cutoff))
    identities = list(queued.order_by('pk').values('pk', 'benefit_id')[:limit])
    sent = 0
    for identity in identities:
        try:
            with transaction.atomic():
                # Same lock order as seller changes: benefit, then notification.
                benefit = CustomerBenefit.objects.select_for_update().get(pk=identity['benefit_id'])
                notice = BenefitMail.objects.select_for_update().get(pk=identity['pk'])
                if notice.sent_at or notice.cancelled_at:
                    continue
                if expected_attempts is not None and notice.attempts != expected_attempts:
                    raise ValidationError('Ce message a été traité depuis votre ouverture. Actualisez son état avant de le relancer.')
                if not force and notice.last_attempt_at and notice.last_attempt_at >= cutoff:
                    continue
                notice.benefit = benefit
                payload = notice.payload
                if blocked_reason(notice):
                    notice.cancelled_at = timezone.now()
                    notice.save(update_fields=['cancelled_at'])
                    continue
                notice.attempts += 1
                notice.last_attempt_at = timezone.now()
                notice.save(update_fields=['attempts', 'last_attempt_at'])
                test = local_capture_enabled()
                context = {
                    'subject': 'Un avoir ou un bon vous attend', 'title': 'Une attention de votre boutique.',
                    'intro': 'La Caverne des Monts a préparé un avantage personnel pour votre prochaine commande.',
                    'action': 'Voir mes avoirs', 'url': settings.SITE_URL + reverse('espace:credits'),
                    'details': payload['details'], 'security': False, 'test': test,
                }
                try:
                    text, html = render_message(context)
                    message = EmailMultiAlternatives(
                        ('[TEST] ' if test else '') + context['subject'] + ' — La Caverne des Monts',
                        text, settings.DEFAULT_FROM_EMAIL, [payload['recipient']],
                    )
                    message.attach_alternative(html, 'text/html')
                    if message.send() != 1:
                        continue
                except Exception:
                    logger.warning('Customer benefit notification delivery failed (notice %s).', notice.pk)
                    continue
                notice.sent_at = timezone.now()
                notice.payload = {**payload, 'delivery_mode': 'local_test' if test else 'provider'}
                notice.save(update_fields=['sent_at', 'payload'])
                CustomerBenefit.objects.filter(pk=benefit.pk).update(notified_at=notice.sent_at)
                sent += 1
        except (BenefitMail.DoesNotExist, CustomerBenefit.DoesNotExist):
            continue
    return sent
