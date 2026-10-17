"""Correct catalogue labels while preserving purchase and return snapshots."""
from .models import VariantLabelCorrection


def identity_lock_reason(variant):
    if not variant.pk:
        return ''
    if variant.reserved or variant.reservations.filter(state='held').exists():
        return 'Un paiement est en cours pour cette variante. Attendez sa résolution avant de corriger la taille ou la couleur.'
    return ''


def identity_change_error(variant, size, color):
    return identity_lock_reason(variant) if (size, color) != (variant.size, variant.color) else ''


def record_label_correction(previous, corrected):
    """Called with the variant lock, in the same transaction as its update."""
    if previous and (previous.size, previous.color) != (corrected.size, corrected.color):
        VariantLabelCorrection.objects.create(
            variant=corrected, product_identifier=previous.product_id,
            previous_size=previous.size, previous_color=previous.color,
            corrected_size=corrected.size, corrected_color=corrected.color,
        )


def matches_historical_label(variant, size, color, product_id):
    """Only traced corrections can link an old purchase to today's label."""
    if variant.product_id != product_id:
        return False
    current = (variant.size, variant.color)
    if (size, color) == current:
        return True
    pending = [(size, color)]
    corrections = list(variant.label_corrections.filter(product_identifier=product_id)
        .values_list('previous_size', 'previous_color', 'corrected_size', 'corrected_color'))
    visited = set()
    while pending:
        label = pending.pop()
        if label == current:
            return True
        if label in visited:
            continue
        visited.add(label)
        pending.extend((new_size, new_color) for old_size, old_color, new_size, new_color
            in corrections if (old_size, old_color) == label)
    return False
