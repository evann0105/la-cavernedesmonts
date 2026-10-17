"""Manual stock movements, serialized with checkout and inventory edits."""
from django.contrib.admin.models import CHANGE, LogEntry
from django.core.exceptions import PermissionDenied
from django.db import transaction

from connexion.email_verification import email_access_allowed
from core.catalog_access import can_manage_catalog
from core.models import Product
from .models import Variant

MAX_QUANTITY = 1000000
OPERATIONS = {'add': 'Ajout manuel', 'remove': 'Retrait manuel', 'set': 'Comptage corrigé'}


class AdjustmentError(ValueError):
    pass


@transaction.atomic
def adjust_stock(product_id, variant_id, *, actor, operation, amount, revision, reason=''):
    if not can_manage_catalog(actor) or not email_access_allowed(actor):
        raise PermissionDenied
    if operation not in OPERATIONS:
        raise AdjustmentError('Choisissez Ajouter, Retirer ou Corriger le comptage.')
    if type(amount) is not int or not 0 <= amount <= MAX_QUANTITY or (operation != 'set' and amount == 0):
        raise AdjustmentError('Indiquez un nombre entier valide. Pour un ajout ou un retrait, indiquez au moins une unité.')
    if type(revision) is not int or revision < 0:
        raise AdjustmentError('Rechargez la page avant de modifier le stock.')
    if not isinstance(reason, str) or len(reason) > 200:
        raise AdjustmentError('Le motif ne doit pas dépasser 200 caractères.')
    reason = ' '.join(reason.split())
    product = Product.objects.select_for_update().get(pk=product_id)
    variant = Variant.objects.select_for_update().get(pk=variant_id, product=product)
    if variant.revision != revision:
        raise AdjustmentError('Le stock a changé depuis l’ouverture de cette page. Rechargez les quantités avant de recommencer ; aucun mouvement supplémentaire n’a été appliqué.')
    before = variant.quantity
    if before is None and operation != 'set':
        raise AdjustmentError('Ce stock est à compter. Choisissez « Corriger le comptage » et indiquez d’abord la quantité réellement présente.')
    after = amount if operation == 'set' else before + (amount if operation == 'add' else -amount)
    if after < 0:
        raise AdjustmentError('Vous ne pouvez pas retirer plus d’articles que le stock physique.')
    if after < variant.reserved:
        raise AdjustmentError(f'{variant.reserved} unité(s) sont réservées pour des commandes en cours. Vous pouvez retirer au maximum {variant.available} unité(s) disponibles.')
    if after > MAX_QUANTITY:
        raise AdjustmentError('Le stock ne peut pas dépasser 1 000 000 d’unités par variante.')
    if after == before:
        return variant, before
    variant.quantity = after
    variant.revision += 1
    variant.save(update_fields=['quantity', 'revision', 'updated_at'])
    previous = 'à compter' if before is None else str(before)
    message = (f'{OPERATIONS[operation]} : {amount} unité(s). Stock physique : {previous} → {after}. '
               f'Taille : {variant.size}. Couleur : {variant.color}. Réservé : {variant.reserved}.')
    if reason:
        message += ' Motif : ' + reason
    LogEntry.objects.log_actions(user_id=actor.pk, queryset=Variant.objects.filter(pk=variant.pk),
                                 action_flag=CHANGE, change_message=message)
    return variant, before
