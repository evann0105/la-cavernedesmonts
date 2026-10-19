"""Archive only individually verified catalogue duplicates, preserving their records.

This does not merge inventory, import old shop quantities or rewrite order snapshots.
The stable source URLs in the reviewed manifest are mandatory identity checks.
"""
from dataclasses import dataclass

from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Max

from .models import Product, ProductImage


@dataclass(frozen=True)
class DuplicateArchiveResult:
    duplicate: Product
    canonical: Product
    changed: bool
    copied_images: int = 0


def _identifier(product):
    value = product.pk if isinstance(product, Product) else product
    if not isinstance(value, int) or isinstance(value, bool):
        raise CommandError('Les deux fiches de produit doivent déjà être enregistrées.')
    return value


@transaction.atomic
def archive_verified_duplicate(source, target, *, expected_source_url, expected_target_url, evidence=None, apply=True):
    """Validate before archiving; ``apply=False`` performs exactly the same checks.

    Lock both products in primary-key order, refuse alias chains and any source
    stock configuration or reduction. Uploaded gallery entries share existing file
    names on the canonical record; no physical file is copied or removed.
    """
    source_id, target_id = _identifier(source), _identifier(target)
    if source_id == target_id:
        raise CommandError('Une fiche ne peut pas être le doublon d’elle-même.')
    locked = {
        product.pk: product
        for product in Product.objects.select_for_update().filter(pk__in=sorted((source_id, target_id))).order_by('pk')
    }
    if len(locked) != 2:
        raise CommandError('Une des fiches du rapprochement est introuvable.')
    duplicate, canonical = locked[source_id], locked[target_id]
    if not expected_source_url or not expected_target_url or duplicate.source_url != expected_source_url or canonical.source_url != expected_target_url:
        raise CommandError('Les adresses source ne correspondent plus aux fiches vérifiées ; aucune modification appliquée.')
    if canonical.duplicate_of_id is not None:
        raise CommandError('La fiche conservée doit être publiée et ne pas être un autre doublon.')
    if duplicate.duplicate_of_id not in (None, canonical.pk):
        raise CommandError('Ce doublon a déjà une autre fiche de référence.')
    if duplicate.duplicate_records.exists():
        raise CommandError('Cette fiche est déjà la référence d’autres doublons ; une vérification manuelle est nécessaire.')
    if duplicate.duplicate_of_id == canonical.pk and not duplicate.is_published and not duplicate.is_featured:
        return DuplicateArchiveResult(duplicate, canonical, False)
    if not canonical.is_published:
        raise CommandError('La fiche conservée doit être publiée et ne pas être un autre doublon.')
    if duplicate.name.strip().casefold() != canonical.name.strip().casefold() or duplicate.price != canonical.price:
        raise CommandError('Le nom ou le prix a changé depuis le rapprochement ; vérifier ces produits avant de les regrouper.')
    # Even a zero/unknown quantity can carry a merchant's deliberate configuration.
    # Keep it intact and require manual review rather than hiding stock by accident.
    if duplicate.stock_variants.exists():
        raise CommandError('Le doublon possède des variantes de stock ; aucune fusion ni modification automatique autorisée.')
    if duplicate.promotions.exists():
        raise CommandError('Le doublon possède des réductions ; vérifier ces opérations avant de l’archiver.')
    if not apply:
        return DuplicateArchiveResult(duplicate, canonical, True)

    existing_files = set(canonical.images.values_list('image', flat=True))
    if canonical.main_image:
        existing_files.add(canonical.main_image.name)
    next_order = (canonical.images.aggregate(last=Max('order'))['last'] or 0) + 1
    copied_images = 0
    photos = [(photo.image.name, photo.alt) for photo in duplicate.images.all() if photo.image]
    if duplicate.main_image:
        photos.insert(0, (duplicate.main_image.name, duplicate.name))
    for file_name, alt in photos:
        if not file_name or file_name in existing_files:
            continue
        ProductImage.objects.create(product=canonical, image=file_name, alt=alt, order=next_order)
        existing_files.add(file_name)
        next_order += 1
        copied_images += 1

    sources = dict(duplicate.information_sources or {})
    sources['_duplicate_review'] = {
        'canonical_slug': canonical.slug,
        'duplicate_source_url': expected_source_url,
        'canonical_source_url': expected_target_url,
        'evidence': evidence or {},
    }
    duplicate.duplicate_of = canonical
    duplicate.is_published = False
    duplicate.is_featured = False
    duplicate.information_sources = sources
    duplicate.save(update_fields=['duplicate_of', 'is_published', 'is_featured', 'information_sources'])
    return DuplicateArchiveResult(duplicate, canonical, True, copied_images)
