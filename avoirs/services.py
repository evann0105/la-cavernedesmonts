"""All mutations lock the benefit; checkout callers first lock their order."""
from decimal import Decimal, ROUND_HALF_UP
from functools import partial

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from config.encrypted_fields import email_digest
from connexion.email_verification import email_verified
from .models import BenefitEvent, BenefitMail, BenefitRedemption, CustomerBenefit, generate_code


class BenefitError(ValueError):
    pass


EDIT_FIELDS = ('recipient_email', 'user_id', 'bound_user_id', 'label', 'kind', 'value', 'minimum_subtotal', 'starts_at', 'ends_at', 'active')


def cents(amount):
    return int((Decimal(amount) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _require_transaction():
    if not connection.in_atomic_block:
        raise RuntimeError('Cette opération doit être réalisée dans une transaction verrouillant la commande.')


def _customer_matches(benefit, user):
    return (email_verified(user) and (not benefit.bound_user_id or benefit.bound_user_id == user.pk)
            and (not benefit.user_id or benefit.user_id == user.pk)
            and constant_time_compare(benefit.recipient_email_hash, email_digest(user.email)))


def quote(benefit, user, subtotal, at=None, order=None):
    """Validate a customer-specific code without consuming it; return integer cents."""
    if not _customer_matches(benefit, user):
        raise BenefitError('Ce code est réservé à son destinataire. Connectez-vous au compte dont l’adresse e-mail est confirmée.')
    if isinstance(subtotal, bool) or not isinstance(subtotal, int) or subtotal <= 0:
        raise BenefitError('Ajoutez des articles au panier avant d’utiliser ce code.')
    live = benefit.redemptions.filter(state__in=['held', 'used']).first()
    if live:
        if live.state == 'used':
            raise BenefitError('Ce code a déjà été utilisé.')
        if not order or live.order_id != order.pk:
            raise BenefitError('Ce code est réservé à un paiement en cours. Terminez ou annulez ce paiement avant de le réutiliser.')
        if live.snapshot.get('user_id') != user.pk or live.snapshot.get('subtotal') != subtotal:
            raise BenefitError('Le panier a changé. Annulez le paiement en cours avant de réutiliser ce code.')
        return {'discount_amount': live.discount_amount, 'snapshot': live.snapshot}
    now = at or timezone.now()
    if not benefit.active:
        raise BenefitError('Ce code est désactivé.')
    if benefit.starts_at and now < benefit.starts_at:
        raise BenefitError('Ce code n’est pas encore valable.')
    if benefit.ends_at and now >= benefit.ends_at:
        raise BenefitError('Ce code est expiré.')
    minimum = cents(benefit.effective_minimum)
    if subtotal < minimum:
        raise BenefitError(f'Ce code nécessite au moins {minimum / 100:.2f} € d’articles, hors livraison.'.replace('.', ','))
    if benefit.kind == 'percent':
        amount = int((Decimal(subtotal) * benefit.value / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    else:
        amount = min(cents(benefit.value), subtotal)
    if amount <= 0:
        raise BenefitError('Cette remise ne peut pas s’appliquer au montant actuel du panier.')
    return {'discount_amount': amount, 'snapshot': {
        'version': 1, 'benefit_id': benefit.pk, 'code': benefit.code, 'label': benefit.label,
        'kind': benefit.kind, 'value': str(benefit.value), 'minimum_subtotal': str(benefit.minimum_subtotal),
        'effective_minimum': str(benefit.effective_minimum), 'starts_at': benefit.starts_at.isoformat() if benefit.starts_at else None,
        'ends_at': benefit.ends_at.isoformat() if benefit.ends_at else None,
        'user_id': user.pk, 'recipient_email_hash': benefit.recipient_email_hash,
        'subtotal': subtotal, 'discount_amount': amount, 'benefit_revision': benefit.updated_at.isoformat(),
    }}


def reserve_locked(order, benefit, user):
    _require_transaction()
    if order.status != 'pending' or not user or order.user_id != user.pk:
        raise BenefitError('Ce code doit être utilisé depuis votre compte, pour une commande en attente.')
    benefit = CustomerBenefit.objects.select_for_update().get(pk=getattr(benefit, 'pk', benefit))
    previous = BenefitRedemption.objects.filter(order=order).first()
    if previous:
        if previous.benefit_id != benefit.pk or previous.state != 'held':
            raise BenefitError('Cette tentative de paiement ne peut pas recevoir un autre code.')
        quote(benefit, user, order.subtotal, order=order)
        return previous
    quoted = quote(benefit, user, order.subtotal, order=order)
    try:
        with transaction.atomic():
            redemption = BenefitRedemption.objects.create(benefit=benefit, order=order,
                discount_amount=quoted['discount_amount'], snapshot=quoted['snapshot'])
    except IntegrityError as exc:
        raise BenefitError('Ce code est déjà réservé ou utilisé. Actualisez votre panier.') from exc
    if not benefit.user_id:
        # Only a verified recipient may claim an email-only grant.
        CustomerBenefit.objects.filter(pk=benefit.pk, user__isnull=True).update(user=user, bound_user_id=user.pk)
    BenefitEvent.objects.create(benefit=benefit, label='Code réservé pour un paiement.')
    return redemption


def _redemption_locked(order):
    _require_transaction()
    identity = BenefitRedemption.objects.filter(order=order).values('pk', 'benefit_id').first()
    if not identity:
        return None
    CustomerBenefit.objects.select_for_update().get(pk=identity['benefit_id'])
    return BenefitRedemption.objects.select_for_update().get(pk=identity['pk'])


def release_locked(order):
    redemption = _redemption_locked(order)
    if redemption and redemption.state == 'held':
        if order.status == 'paid':
            raise BenefitError('Une commande payée ne peut pas libérer un code réservé.')
        redemption.state = 'released'
        redemption.released_at = timezone.now()
        redemption.save(update_fields=['state', 'released_at'])
        BenefitEvent.objects.create(benefit_id=redemption.benefit_id, label='Réservation libérée après annulation ou expiration du paiement.')
    return redemption


def consume_locked(order, session):
    """Consume only inside settlement's atomic transaction, after payment proof."""
    redemption = _redemption_locked(order)
    if not redemption:
        return None
    if redemption.state == 'used' and order.status == 'paid':
        return redemption
    if redemption.state != 'held' or order.status != 'pending':
        raise BenefitError('La réservation de ce code ne correspond plus au paiement.')
    from paiement.line_totals import payment_confirmed
    if not payment_confirmed(session):
        raise BenefitError('Le paiement n’est pas confirmé : le code reste réservé.')
    discount = (session.get('total_details') or {}).get('amount_discount', 0)
    shipping = (session.get('total_details') or {}).get('amount_shipping', 0)
    if (isinstance(discount, bool) or not isinstance(discount, int) or discount != redemption.discount_amount
            or order.subtotal != redemption.snapshot.get('subtotal')
            or order.user_id != redemption.snapshot.get('user_id')
            or session.get('currency') != 'eur' or session.get('amount_subtotal') != order.subtotal
            or isinstance(shipping, bool) or not isinstance(shipping, int) or shipping < 0
            or session.get('amount_total') != order.subtotal - discount + shipping
            or session.get('metadata', {}).get('order_id') != str(order.pk)
            or session.get('client_reference_id') != str(order.pk)
            or (order.stripe_session_id and order.stripe_session_id != session.get('id'))):
        raise BenefitError('La remise du paiement ne correspond pas au code réservé. Une vérification par la boutique est nécessaire.')
    redemption.state = 'used'
    redemption.used_at = timezone.now()
    redemption.save(update_fields=['state', 'used_at'])
    BenefitEvent.objects.create(benefit_id=redemption.benefit_id, label='Code utilisé après confirmation du paiement.')
    return redemption


def save_benefit(instance, actor, notify=True, creation_key=None, reassign=False):
    """Create/update through the seller UI. No external e-mail before commit."""
    with transaction.atomic():
        previous = None
        changed = True
        if notify and (not instance.active or (instance.ends_at and timezone.is_aware(instance.ends_at) and instance.ends_at <= timezone.now())):
            raise BenefitError('Un code inactif ou expiré ne peut pas être envoyé. Décochez « Informer le client par e-mail » pour l’enregistrer sans notification.')
        if not instance.pk and creation_key:
            existing = CustomerBenefit.objects.filter(creation_key=creation_key).first()
            if existing:
                if existing.created_by_id != actor.pk:
                    raise BenefitError('Cette création a déjà été traitée.')
                return existing
            instance.creation_key = creation_key
        if instance.pk:
            previous = CustomerBenefit.objects.select_for_update().get(pk=instance.pk)
            if previous.code != instance.code:
                raise BenefitError('Un code émis ne peut pas être changé.')
            if previous.updated_at != instance.updated_at:
                raise BenefitError('Cet avoir a été modifié entre-temps. Rechargez la page avant de recommencer.')
        instance.recipient_email = (instance.recipient_email or '').strip().lower()
        instance.recipient_email_hash = email_digest(instance.recipient_email)
        if previous and previous.recipient_email == instance.recipient_email and not reassign:
            # An e-mail address that is later reused by another account must
            # never transfer a benefit already linked to the original account.
            instance.user_id = previous.user_id
            instance.bound_user_id = previous.bound_user_id or previous.user_id
        else:
            instance.user = get_user_model().objects.filter(email_hash=instance.recipient_email_hash).first()
            instance.bound_user_id = instance.user_id
        if notify and instance.bound_user_id and not instance.user_id:
            raise BenefitError('Le compte du destinataire a été supprimé. Réattribuez explicitement ce code avant de le notifier, ou enregistrez sans e-mail.')
        if previous:
            changed = any(getattr(previous, field) != getattr(instance, field) for field in EDIT_FIELDS)
            if (changed or reassign) and previous.redemptions.filter(state__in=['held', 'used']).exists():
                raise BenefitError('Un code réservé par un paiement ou déjà utilisé ne peut plus être modifié.')
            if not changed and not reassign:
                # An unchanged save must preserve the exact business revision
                # and any notification awaiting a retry after an SMTP failure.
                if notify:
                    from .mail import enqueue, deliver_pending
                    enqueue(previous)
                    transaction.on_commit(partial(deliver_pending, benefit_id=previous.pk), robust=True)
                return previous
        if not previous:
            instance.created_by = actor
        elif changed:
            instance.notified_at = None
        for attempt in range(5):
            try:
                with transaction.atomic():
                    instance.full_clean()
                    instance.save()
                break
            except (IntegrityError, ValidationError) as exc:
                if not previous and instance.creation_key:
                    existing = CustomerBenefit.objects.filter(creation_key=instance.creation_key).first()
                    if existing:
                        if existing.created_by_id != actor.pk:
                            raise BenefitError('Cette création a déjà été traitée.') from exc
                        return existing
                # The generated code is never editable. A rare collision must
                # produce a fresh value, never reassign an existing code.
                if previous or not CustomerBenefit.objects.filter(code=instance.code).exists():
                    raise
                instance.code = generate_code()
        else:
            raise BenefitError('La génération du code a échoué. Réessayez.')
        if previous:
            BenefitMail.objects.filter(benefit=instance, sent_at__isnull=True, cancelled_at__isnull=True).update(cancelled_at=timezone.now())
        BenefitEvent.objects.create(benefit=instance, actor=actor, label='Avoir / bon modifié.' if previous else 'Avoir / bon créé.')
        if previous and (reassign or previous.recipient_email_hash != instance.recipient_email_hash):
            BenefitEvent.objects.create(benefit=instance, actor=actor, label='Destinataire réattribué explicitement par la boutique.')
        if notify:
            from .mail import enqueue, deliver_pending
            enqueue(instance)
            transaction.on_commit(partial(deliver_pending, benefit_id=instance.pk), robust=True)
        return instance


def notify_benefit(benefit, actor=None, key=None):
    with transaction.atomic():
        benefit = CustomerBenefit.objects.select_for_update().get(pk=benefit.pk)
        if benefit.status in ('used', 'inactive', 'expired'):
            raise BenefitError('Ce code n’est plus utilisable : aucune notification n’a été envoyée.')
        if (benefit.bound_user_id and not benefit.user_id) or (benefit.user_id and email_digest(benefit.user.email) != benefit.recipient_email_hash):
            raise BenefitError('L’adresse du compte client a changé. Corrigez le destinataire avant de le notifier.')
        from .mail import enqueue, deliver_pending
        mail = enqueue(benefit, key=key, resend=True)
        BenefitEvent.objects.create(benefit=benefit, actor=actor, label='Notification client demandée.')
        transaction.on_commit(partial(deliver_pending, benefit_id=benefit.pk), robust=True)
        return mail
