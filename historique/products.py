"""Editorial product versions; inventory and commercial transactions stay live."""
from contextlib import contextmanager
from copy import copy
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.templatetags.static import static
from django.utils.translation import override

from core.models import Category, Product, ProductColor, ProductImage
from core.product_images import imported_gallery_urls
from stocks.identity import identity_change_error, record_label_correction
from stocks.models import Variant
from .exceptions import HistoryConflict, HistoryError
from .media import archive_files, asset_name, media_name
from .services import authorized, fingerprint, record

FIELDS = ('category_id', 'name', 'price', 'description', 'brand', 'model_reference',
    'composition', 'brand_contact', 'information_sources', 'information_review_notes',
    'benefits', 'translations', 'main_image', 'main_color', 'is_featured', 'is_published',
    'include_imported_gallery', 'static_image', 'sizes', 'article_type', 'availability')
EDITOR_SALT = 'historique-fiche-produit-v1'


def snapshot_product(product):
    # Never inspect a ModelForm-mutated instance or prefetched old relations.
    product = Product.objects.get(pk=product.pk)
    fields = {key: getattr(product, key) for key in FIELDS}
    fields['main_image'] = product.main_image.name or ''
    fields['price'] = str(product.price)
    collections = list(product.collections.order_by('pk').values_list('pk', flat=True))
    gallery = [{'id': row.pk, 'image': row.image.name, 'alt': row.alt, 'order': row.order}
               for row in product.images.order_by('order', 'pk')]
    colors = [{'id': row.pk, 'name': row.name, 'image': row.image.name}
              for row in product.color_photos.order_by('pk')]
    variants = [{'id': row.pk, 'size': row.size, 'color': row.color, 'reference': row.reference,
                 'price': str(row.price) if row.price is not None else None}
                for row in product.stock_variants.order_by('pk')]
    prefix = static('')
    initial = [static(product.static_image)] if product.static_image else []
    imported = [{'kind': 'static', 'name': unquote(url[len(prefix):]), 'alt': product.name}
                for url in imported_gallery_urls(product, initial)[len(initial):]
                if url.startswith(prefix)]
    ids = collections + ([product.category_id] if product.category_id else [])
    with override('fr'):
        resolved_benefits = list(product.benefit_lines) if product.benefits is None else []
    return {'schema': 1, 'fields': fields, 'collections': collections, 'gallery': gallery,
            'colors': colors, 'variants': variants, 'imported_gallery': imported,
            'resolved_benefits': resolved_benefits,
            'category_labels': {str(pk): name for pk, name in Category.objects.filter(pk__in=ids).values_list('pk', 'name')}}


def snapshot_assets(snapshot):
    fields = snapshot['fields']
    assets = [{'kind': 'media', 'name': fields.get('main_image', '')},
              {'kind': 'static', 'name': fields.get('static_image', '')}]
    assets.extend({'kind': 'media', 'name': row['image']} for row in snapshot['gallery'] + snapshot['colors'])
    assets.extend({'kind': row['kind'], 'name': row['name']} for row in snapshot['imported_gallery'])
    return [row for row in assets if row['name']]


def record_product(product, *, actor=None, label='', source='edit', restored_from=None, snapshot=None):
    state = snapshot if snapshot is not None else snapshot_product(product)
    return record('product', product.pk, state, actor=actor, label=label, source=source,
                  object_label=state['fields']['name'], restored_from=restored_from,
                  assets=snapshot_assets(state))


def record_change(product, before, *, actor=None, label='Fiche produit modifiée'):
    """Record only real editorial changes, never a stock count or sale alone."""
    after = snapshot_product(product)
    if before is not None and fingerprint(before) == fingerprint(after):
        return None
    if before is not None:
        record_product(product, actor=actor, source='baseline', snapshot=before,
                       label='État conservé avant modification')
    return record_product(product, actor=actor, label=label, snapshot=after)


@contextmanager
def product_change(product_id, *, actor=None, label='Fiche produit modifiée'):
    with transaction.atomic():
        product = Product.objects.select_for_update().get(pk=product_id)
        before = snapshot_product(product)
        yield product
        record_change(product, before, actor=actor, label=label)


def editor_token(product, actor):
    return signing.dumps({'product': product.pk, 'actor': actor.pk,
                          'fingerprint': fingerprint(snapshot_product(product))}, salt=EDITOR_SALT)


def validate_editor_token(product, actor, token):
    try:
        payload = signing.loads(token, salt=EDITOR_SALT, max_age=24 * 60 * 60)
    except (signing.BadSignature, TypeError, ValueError) as error:
        raise HistoryConflict('Cette fiche doit être rouverte avant d’être enregistrée. Votre saisie est conservée ci-dessous pour la comparer.') from error
    if (not isinstance(payload, dict) or payload.get('product') != product.pk or payload.get('actor') != actor.pk
            or payload.get('fingerprint') != fingerprint(snapshot_product(product))):
        raise HistoryConflict('Cette fiche a changé depuis son ouverture. Votre saisie est conservée ; ouvrez la fiche à jour dans un autre onglet avant de reporter vos modifications.')


def _state(version):
    state = version.snapshot
    if (version.scope != 'product' or not isinstance(state, dict) or state.get('schema') != 1
            or set(state.get('fields', {})) != set(FIELDS)
            or any(not isinstance(state.get(key), list) for key in ('collections', 'gallery', 'colors', 'variants', 'imported_gallery'))
            or fingerprint(state) != version.fingerprint):
        raise HistoryError('Cette version de fiche est invalide. Aucune restauration n’a été effectuée.')
    if 'resolved_benefits' in state:
        benefits = state['resolved_benefits']
        if (not isinstance(benefits, list) or len(benefits) > 3
                or any(not isinstance(item, str) or not item.strip() or len(item) > 750 for item in benefits)):
            raise HistoryError('Les bénéfices conservés avec cette version ne sont pas valides.')
    return state


def _validate(version, product, variants, *, lock=False):
    state = _state(version)
    fields = state['fields']
    category_ids = set(state['collections']) | ({fields['category_id']} if fields['category_id'] else set())
    categories = Category.objects.filter(pk__in=category_ids).order_by('pk')
    if lock:
        categories = categories.select_for_update()
    categories = list(categories)
    if len(categories) != len(category_ids) or any(category.archived_at for category in categories):
        raise HistoryError('Une rubrique de cette version a été supprimée ou archivée. Réactivez-la avant de restaurer la fiche.')
    if product.duplicate_of_id and fields['is_published']:
        raise HistoryError('Cette fiche est un doublon archivé. Sa restauration ne peut pas la republier.')
    candidate = copy(product)
    for key, value in fields.items():
        setattr(candidate, key, value)
    try:
        candidate.clean_fields(exclude=['source_url', 'duplicate_of', 'main_image', 'slug'])
        if Decimal(fields['price']) <= 0:
            raise ValidationError('Le prix doit être positif.')
    except (ValidationError, InvalidOperation, TypeError, ValueError) as error:
        raise HistoryError('Les informations de cette version ne permettent plus d’enregistrer une fiche valide.') from error
    if fields['is_published'] and (not fields['category_id'] or not fields['description'].strip()
            or not (fields['main_image'] or fields['static_image'])):
        raise HistoryError('Cette ancienne fiche publiée est incomplète : rubrique, description et photo principale sont nécessaires.')
    current = {variant.pk: variant for variant in variants}
    targets = {}
    for row in state['variants']:
        if row['id'] not in current:
            raise HistoryError('Une taille ou couleur de cette version a été supprimée. La restauration ne recrée pas un stock historique ; corrigez la fiche actuelle.')
        if row['id'] in targets:
            raise HistoryError('Cette version contient une taille ou couleur répétée.')
        variant = current[row['id']]
        candidate_variant = copy(variant)
        for key in ('size', 'color', 'reference', 'price'):
            setattr(candidate_variant, key, row[key])
        try:
            candidate_variant.clean_fields(exclude=['product', 'quantity', 'reserved', 'revision', 'updated_at'])
        except ValidationError as error:
            raise HistoryError('Une ancienne taille, couleur, référence ou son prix n’est plus valide.') from error
        problem = identity_change_error(variant, row['size'], row['color'])
        if problem:
            raise HistoryError(problem)
        # Avoid swaps through temporary identities: other subsystems depend on
        # permanent variant ids and purchase label correction trails.
        combo = (row['size'].casefold(), row['color'].casefold())
        if any(other.pk != variant.pk and (other.size.casefold(), other.color.casefold()) == combo for other in variants):
            raise HistoryError('Une taille et une couleur de cette version sont déjà utilisées par une autre variante. Corrigez cette combinaison avant de restaurer.')
        if combo in targets.values():
            raise HistoryError('Cette version contient une combinaison taille/couleur répétée.')
        targets[row['id']] = combo
    for asset in snapshot_assets(state):
        asset_name(version, asset['kind'], asset['name'])
    for key, model, columns in [('gallery', ProductImage, ('image', 'alt', 'order')),
                                ('colors', ProductColor, ('image', 'name'))]:
        seen = set()
        for row in state[key]:
            if type(row.get('id')) is not int or row['id'] < 1 or row['id'] in seen:
                raise HistoryError('Cette version contient une référence de photo invalide.')
            seen.add(row['id'])
            if model.objects.filter(pk=row['id']).exclude(product=product).exists():
                raise HistoryError('Une référence de photo a été réaffectée. Aucune restauration n’a été effectuée.')
            try:
                model(**{column: row[column] for column in columns}).clean_fields(exclude=['product'])
            except ValidationError as error:
                raise HistoryError('Une photo de cette version contient des informations invalides.') from error
    color_names = [row['name'].casefold() for row in state['colors']]
    if len(color_names) != len(set(color_names)):
        raise HistoryError('Cette version contient des photos de couleur répétées.')
    return state


def validate_restore(version):
    product = Product.objects.filter(pk=version.object_id).first()
    if product is None:
        raise HistoryError('Cette fiche a été supprimée. Sa version reste consultable, mais ne peut pas être restaurée.')
    _validate(version, product, list(product.stock_variants.order_by('pk')))


def restore_warnings(version):
    state = _state(version)
    known = {row['id'] for row in state['variants']}
    newer = Variant.objects.filter(product_id=version.object_id).exclude(pk__in=known).count()
    warnings = ['Les quantités actuelles, les réservations, les commandes et les paiements sont conservés. Les soldes programmées restent en place.']
    if newer:
        warnings.append(f'{newer} taille(s) ou couleur(s) ajoutée(s) depuis cette version seront conservées avec leur stock et leur prix actuels.')
    if state['imported_gallery']:
        warnings.append('Les photos importées de cette version seront conservées comme photos de la galerie.')
    return warnings


def _retained_media(version, kind, name):
    """Materialize recovered legacy assets only during the restore POST."""
    try:
        return asset_name(version, kind, name, prefer_archive=True)[1]
    except HistoryError:
        if any(asset.get('kind') == kind and asset.get('name') == name and asset.get('sha256')
               for asset in version.assets):
            raise
        # validate_restore already checked the original against this version.
        archived = archive_files([{'kind': kind, 'name': name}])[0]
        if not archived.get('archive'):
            raise HistoryError('Une ancienne photo est introuvable. Aucune restauration n’a été effectuée.')
        return archived['archive']


@transaction.atomic
def restore_product(version, *, actor, expected_fingerprint):
    authorized(actor)
    try:
        product = Product.objects.select_for_update().get(pk=version.object_id)
    except Product.DoesNotExist as error:
        raise HistoryError('Cette fiche a été supprimée et ne peut pas être restaurée.') from error
    variants = list(Variant.objects.select_for_update().filter(product=product).order_by('pk'))
    before = snapshot_product(product)
    if fingerprint(before) != expected_fingerprint:
        raise HistoryConflict('La fiche a changé depuis votre vérification. Comparez à nouveau les versions avant de restaurer.')
    state = _validate(version, product, variants, lock=True)
    record_product(product, actor=actor, snapshot=before, source='checkpoint', label='État conservé avant restauration')
    fields = dict(state['fields'])
    if fields['benefits'] is None and 'resolved_benefits' in state:
        fields['benefits'] = '\n'.join(state['resolved_benefits'])
    fields['main_image'] = media_name(version, fields['main_image'])
    if fields['static_image']:
        name = _retained_media(version, 'static', fields['static_image'])
        if not fields['main_image']:
            fields['main_image'] = name
        fields['static_image'] = ''
    # Freeze the exact gallery; do not rediscover later imported files.
    fields['include_imported_gallery'] = False
    for key, value in fields.items():
        setattr(product, key, value)
    product.price = Decimal(fields['price'])
    product_price_changed = product.price != Decimal(before['fields']['price'])
    product.save(update_fields=list(fields), record_price_history=False)
    product.collections.set(state['collections'])
    product.images.all().delete()
    product.color_photos.all().delete()
    for row in state['gallery']:
        ProductImage.objects.create(pk=row['id'], product=product, image=media_name(version, row['image']), alt=row['alt'], order=row['order'])
    order = max((row['order'] for row in state['gallery']), default=-1) + 1
    for offset, row in enumerate(state['imported_gallery']):
        name = _retained_media(version, row['kind'], row['name'])
        ProductImage.objects.create(product=product, image=name, alt=row['alt'], order=order + offset)
    for row in state['colors']:
        ProductColor.objects.create(pk=row['id'], product=product, name=row['name'], image=media_name(version, row['image']))
    current = {variant.pk: variant for variant in variants}
    changed_prices = []
    for row in state['variants']:
        variant = current[row['id']]
        previous = copy(variant)
        new_price = Decimal(row['price']) if row['price'] is not None else None
        if (variant.size, variant.color, variant.reference, variant.price) == (row['size'], row['color'], row['reference'], new_price):
            continue
        variant.size, variant.color, variant.reference, variant.price = row['size'], row['color'], row['reference'], new_price
        variant.revision += 1
        variant.save(update_fields=['size', 'color', 'reference', 'price', 'revision', 'updated_at'], record_price_history=False)
        if previous.price != new_price:
            changed_prices.append(variant)
        record_label_correction(previous, variant)
    # Observe only the final prices visible after this atomic restoration, not
    # temporary inherited prices between the product and variant writes.
    from core.pricing import record_product_price, record_variant_price
    if product_price_changed:
        record_product_price(product)
    for variant in changed_prices:
        if variant.price is not None or not product_price_changed:
            record_variant_price(variant)
    return record_product(product, actor=actor, source='restore', restored_from=version,
                          label=f'Restauration de la version {version.sequence}')


def preview_response(request, version):
    from .product_preview import response
    return response(request, version)
