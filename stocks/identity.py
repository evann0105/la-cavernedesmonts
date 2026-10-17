"""Correct unused variant labels without rewriting a purchased identity."""
from django.db.models import Q
from paiement.models import Order
from retours.models import ReturnLine


def identity_lock_reason(variant):
    if not variant.pk:
        return ''
    if variant.reserved:
        return 'Un paiement est en cours pour cette variante. Attendez sa résolution avant de corriger la taille ou la couleur.'
    if (variant.reservations.exists() or ReturnLine.objects.filter(variant_id=variant.pk).exists()
            or Order.objects.filter(Q(items__contains=[{'variant':variant.pk}])
                | Q(items__contains=[{'variant':str(variant.pk)}])).exists()):
        return 'Cette variante figure déjà dans une commande, une réservation ou un retour. Sa taille et sa couleur sont conservées pour préserver cet historique ; ajoutez une variante pour un autre libellé.'
    return ''


def identity_change_error(variant, size, color):
    return identity_lock_reason(variant) if (size, color) != (variant.size, variant.color) else ''
