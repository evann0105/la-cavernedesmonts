"""Catalogue batch history and previewed, field-scoped undo.

Only values changed by the original batch are restored. Product identities, stock,
orders, variant prices and promotions are deliberately outside this service.
"""
import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.contrib.admin.models import CHANGE, LogEntry
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from connexion.email_verification import email_access_allowed
from .catalog_access import can_manage_catalog
from .models import CatalogChange, Category, Product

UNDO_SALT = 'catalogue-field-undo-v1'
UNDO_MAX_AGE = 7200
FIELDS = ('category', 'brand', 'article_type', 'is_published', 'is_featured', 'availability', 'price')
LABELS = {'category': 'Rubrique principale', 'brand': 'Marque', 'article_type': 'Type d’article',
          'is_published': 'Visibilité', 'is_featured': 'Mise en avant',
          'availability': 'Disponibilité indicative', 'price': 'Prix général',
          'collections': 'Rubriques complémentaires'}


class CatalogUndoConflict(ValidationError):
    pass


def _authorize(actor):
    if actor is None or not can_manage_catalog(actor) or not email_access_allowed(actor):
        raise PermissionDenied


def _value(product, field):
    if field == 'category':
        return product.category_id
    if field == 'collections':
        return sorted(item.pk for item in product.collections.all())
    if field == 'price':
        return str(Decimal(str(product.price)).quantize(Decimal('.01')))
    return getattr(product, field)


def _label(field, value, categories):
    if field == 'category':
        return categories.get(value, 'Sans rubrique' if value is None else 'Rubrique supprimée')
    if field == 'collections':
        return ', '.join(sorted(categories.get(pk, 'Rubrique supprimée') for pk in value)) or 'Aucune'
    if field == 'price':
        return f'{Decimal(value):.2f} €'.replace('.', ',')
    if field == 'is_published':
        return 'Publié' if value else 'Brouillon / retiré'
    if field == 'is_featured':
        return 'À la une' if value else 'Sans mise en avant'
    if field in ('article_type', 'availability'):
        return str(dict(Product._meta.get_field(field).choices).get(value, 'Classement automatique'))
    return value or 'Non renseignée'


def record_catalog_bulk(actor, products, changes):
    """Called inside apply_bulk_confirmation's transaction, before writing rows."""
    _authorize(actor)
    categories = dict(Category.objects.values_list('pk', 'name'))
    items = []
    for product in products:
        differences = {}
        for field in FIELDS:
            if field not in changes:
                continue
            before = _value(product, field)
            after = str(Decimal(changes[field]).quantize(Decimal('.01'))) if field == 'price' else changes[field]
            if before != after:
                differences[field] = {'before': before, 'after': after,
                    'before_label': _label(field, before, categories), 'after_label': _label(field, after, categories)}
        before = _value(product, 'collections')
        after = sorted((set(before) | set(changes.get('collections_add', []))) - set(changes.get('collections_remove', [])))
        if before != after:
            differences['collections'] = {'before': before, 'after': after,
                'before_label': _label('collections', before, categories), 'after_label': _label('collections', after, categories)}
        if differences:
            items.append({'product_id': product.pk, 'product_name': product.name, 'changes': differences})
    if not items:
        return None
    return CatalogChange.objects.create(kind='bulk', actor=actor, actor_label=actor.get_username(), items=items)


def history_rows(change):
    """Presentation uses the labels saved at the time, even after a rename/deletion."""
    return [{'product_id': item['product_id'], 'product_name': item['product_name'],
             'details': [{'field': field, 'label': LABELS[field],
                          'before': value['before_label'], 'after': value['after_label']}
                         for field, value in item['changes'].items()]}
            for item in change.items]


def _get_change(change_id, *, lock=False):
    if type(change_id) is not int or change_id < 1:
        raise CatalogUndoConflict('Cette opération n’existe pas. Revenez à l’historique.')
    query = CatalogChange.objects.select_for_update() if lock else CatalogChange.objects
    change = query.filter(pk=change_id).first()
    if change is None or change.kind != 'bulk':
        raise CatalogUndoConflict('Cette opération ne peut pas être annulée depuis le catalogue.')
    return change


def _digest(change):
    return hashlib.sha256(json.dumps(change.items, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _validated_items(change):
    items = change.items
    if not isinstance(items, list) or not 1 <= len(items) <= 500:
        raise CatalogUndoConflict('Cet historique ne contient pas les informations nécessaires à une annulation.')
    ids = set()
    for item in items:
        if (not isinstance(item, dict) or set(item) != {'product_id', 'product_name', 'changes'}
                or type(item['product_id']) is not int or item['product_id'] < 1
                or item['product_id'] in ids or not isinstance(item['product_name'], str)
                or not isinstance(item['changes'], dict) or not item['changes']
                or set(item['changes']) - set(LABELS)):
            raise CatalogUndoConflict('Les détails de cette opération ne peuvent pas être vérifiés.')
        ids.add(item['product_id'])
        for field, values in item['changes'].items():
            if (not isinstance(values, dict) or set(values) != {'before', 'after', 'before_label', 'after_label'}
                    or not all(isinstance(values[key], str) for key in ('before_label', 'after_label'))):
                raise CatalogUndoConflict('Les valeurs de cette opération ne peuvent pas être vérifiées.')
            for key in ('before', 'after'):
                value = values[key]
                valid = False
                if field in ('is_published', 'is_featured'):
                    valid = type(value) is bool
                elif field == 'category':
                    valid = value is None or (type(value) is int and value > 0)
                elif field == 'collections':
                    valid = (isinstance(value, list) and all(type(pk) is int and pk > 0 for pk in value)
                             and len(value) == len(set(value)))
                elif field == 'price':
                    try:
                        decimal = Decimal(value) if isinstance(value, str) and len(value) <= 20 else None
                        valid = (decimal is not None and decimal.is_finite() and decimal > 0
                                 and decimal < Decimal('100000000') and decimal.as_tuple().exponent >= -2)
                    except InvalidOperation:
                        pass
                else:
                    valid = isinstance(value, str)
                if not valid:
                    raise CatalogUndoConflict('Une ancienne valeur est invalide. Aucun article n’a été modifié.')
    return sorted(ids)


def _target_categories(change, *, lock=False):
    targets = set()
    for item in change.items:
        differences = item['changes']
        if 'category' in differences and differences['category']['before'] is not None:
            targets.add(differences['category']['before'])
        if 'collections' in differences:
            # Removing a recently archived association is allowed. Reintroducing it is not.
            targets.update(set(differences['collections']['before']) - set(differences['collections']['after']))
    query = Category.objects.select_for_update() if lock else Category.objects
    categories = list(query.filter(pk__in=targets).order_by('pk'))
    if len(categories) != len(targets) or any(category.archived_at is not None for category in categories):
        raise CatalogUndoConflict('Une rubrique à rétablir a été archivée ou supprimée. Restaurez-la avant d’annuler cette modification.')
    return [[category.pk, category.name, category.revision] for category in categories]


def _undo_rows(change, *, lock=False):
    from .catalog_bulk import _validate_publication

    ids = _validated_items(change)
    query = Product.objects.select_for_update(of=('self',)) if lock else Product.objects
    products = list(query.filter(pk__in=ids).prefetch_related('collections').order_by('pk'))
    if [product.pk for product in products] != ids or any(product.duplicate_of_id for product in products):
        raise CatalogUndoConflict('Un article a été supprimé ou archivé comme doublon. Aucun article n’a été modifié.')
    by_id = {product.pk: product for product in products}
    category_state = _target_categories(change, lock=lock)
    category_names = (dict(Category.objects.values_list('pk', 'name')) if any(
        {'category', 'collections'} & set(item['changes']) for item in change.items) else {})
    rows = []
    for item in change.items:
        product = by_id[item['product_id']]
        restored = {}
        details = []
        changes = {}
        for field, values in item['changes'].items():
            if _value(product, field) != values['after']:
                raise CatalogUndoConflict(f'« {product.name} » a été modifié depuis cette opération ({LABELS[field].lower()}). Aucun article n’a été modifié.')
            if field in ('category', 'collections'):
                # Undo restores memberships, not old category names. Keep the original
                # journal intact while previewing and auditing today's labels.
                values = {**values, 'before_label': _label(field, values['before'], category_names),
                          'after_label': _label(field, values['after'], category_names)}
            changes[field] = values
            if field != 'collections':
                # The captured values are trusted server records, but validate them again
                # against the current model before restoring an old schema/value.
                try:
                    if field != 'category':
                        Product._meta.get_field(field).clean(values['before'], product)
                except ValidationError as exc:
                    raise CatalogUndoConflict(f'« {product.name} » : une ancienne valeur ne peut plus être rétablie.') from exc
                restored[field] = values['before']
            details.append({'field': field, 'label': LABELS[field],
                            'before': values['after_label'], 'after': values['before_label']})
        try:
            _validate_publication(product, restored)
        except ValidationError as exc:
            raise CatalogUndoConflict(exc.messages) from exc
        rows.append({'product': product, 'product_id': product.pk, 'product_name': product.name,
                     'details': details, 'changes': changes})
    return rows, category_state


def prepare_catalog_undo(actor, change_id):
    """Read-only preview; the token belongs to this actor and expires in two hours."""
    _authorize(actor)
    change = _get_change(change_id)
    if change.undone_at:
        raise CatalogUndoConflict('Cette opération a déjà été annulée.')
    rows, categories = _undo_rows(change)
    token = signing.dumps({'version': 1, 'actor': actor.pk, 'change': change.pk,
                           'digest': _digest(change), 'categories': categories},
                          salt=UNDO_SALT, compress=True)
    return change, rows, token


def _read_token(actor, token, expected_change_id):
    if not isinstance(token, str) or not token or len(token) > 100000:
        raise CatalogUndoConflict('Cette confirmation est invalide. Préparez à nouveau l’annulation.')
    try:
        payload = signing.loads(token, salt=UNDO_SALT, max_age=UNDO_MAX_AGE)
    except signing.BadSignature as exc:
        raise CatalogUndoConflict('Cette confirmation a expiré ou a été modifiée. Préparez à nouveau l’annulation.') from exc
    if (not isinstance(payload, dict) or set(payload) != {'version', 'actor', 'change', 'digest', 'categories'}
            or payload['version'] != 1 or payload['actor'] != actor.pk
            or type(payload['change']) is not int or payload['change'] < 1
            or not isinstance(payload['digest'], str) or not isinstance(payload['categories'], list)
            or (expected_change_id is not None and payload['change'] != expected_change_id)):
        raise CatalogUndoConflict('Cette confirmation ne correspond pas à cette opération ou à votre session.')
    return payload


@transaction.atomic
def apply_catalog_undo(actor, token, *, expected_change_id=None):
    _authorize(actor)
    payload = _read_token(actor, token, expected_change_id)
    change = _get_change(payload['change'], lock=True)
    if payload['digest'] != _digest(change):
        raise CatalogUndoConflict('L’historique de cette opération a changé. Rechargez-le avant de continuer.')
    if change.undone_at:
        return change, 0, True
    rows, categories = _undo_rows(change, lock=True)
    if categories != payload['categories']:
        raise CatalogUndoConflict('Une rubrique a changé depuis l’aperçu. Préparez à nouveau l’annulation.')
    inverse_items = []
    for row in rows:
        product = row['product']
        update_fields = []
        inverse = {}
        for field, values in row['changes'].items():
            if field == 'collections':
                product.collections.set(values['before'])
            else:
                attribute = 'category_id' if field == 'category' else field
                setattr(product, attribute, Decimal(values['before']) if field == 'price' else values['before'])
                update_fields.append(field)
            inverse[field] = {'before': values['after'], 'after': values['before'],
                              'before_label': values['after_label'], 'after_label': values['before_label']}
        if update_fields:
            product.save(update_fields=update_fields)
        inverse_items.append({'product_id': product.pk, 'product_name': product.name, 'changes': inverse})
        LogEntry.objects.log_actions(user_id=actor.pk, queryset=Product.objects.filter(pk=product.pk), action_flag=CHANGE,
            change_message=f'Annulation de la modification groupée n° {change.pk}. ' + ' ; '.join(
                f"{detail['label']} : {detail['before']} → {detail['after']}" for detail in row['details']))
    CatalogChange.objects.create(kind='undo', actor=actor, actor_label=actor.get_username(), items=inverse_items, reverses=change)
    change.undone_at = timezone.now()
    change.undone_by = actor
    change.undone_by_label = actor.get_username()
    change.save(update_fields=['undone_at', 'undone_by', 'undone_by_label'])
    return change, len(rows), False
