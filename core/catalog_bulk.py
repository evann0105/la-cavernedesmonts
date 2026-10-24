"""Previewed, atomic catalogue edits. Stock and promotions are never changed here."""
import hashlib
import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from connexion.email_verification import email_access_allowed
from .catalog_access import can_manage_catalog, catalog_required
from .catalog_bulk_forms import CatalogBulkForm, changes_form_data
from .models import Category, Product

SELECTION_SALT = 'catalogue-bulk-selection-v1'
CONFIRMATION_SALT = 'catalogue-bulk-confirmation-v1'
MAX_PRODUCTS = 500
MAX_AGE = 7200


class CatalogBulkConflict(ValidationError):
    pass


def _authorize(actor):
    if not can_manage_catalog(actor) or not email_access_allowed(actor):
        raise PermissionDenied


def _ids(values):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= MAX_PRODUCTS:
        raise ValidationError(f'Sélectionnez entre 1 et {MAX_PRODUCTS} produits.')
    result = []
    for value in values:
        if (isinstance(value, bool) or not isinstance(value, (int, str))
                or not str(value).isascii() or not str(value).isdecimal() or len(str(value)) > 19):
            raise ValidationError('La sélection de produits est invalide. Revenez au catalogue.')
        number = int(value)
        if number <= 0 or number > 9223372036854775807:
            raise ValidationError('La sélection de produits est invalide. Revenez au catalogue.')
        result.append(number)
    if len(set(result)) != len(result):
        raise ValidationError('Un article apparaît plusieurs fois dans la sélection. Revenez au catalogue.')
    return sorted(result)


def _products(ids, lock=False):
    query = Product.objects.filter(pk__in=ids).order_by('pk')
    if lock:
        query = query.select_for_update(of=('self',))
    products = list(query.prefetch_related('collections', 'stock_variants').select_related('category'))
    if [item.pk for item in products] != ids or any(item.duplicate_of_id for item in products):
        raise CatalogBulkConflict('Un article a été supprimé ou archivé comme doublon. Revenez au catalogue pour refaire votre sélection.')
    return products


def _fingerprint(product):
    # Include publication prerequisites and editable content, so an old tab cannot
    # approve a different product state. Ignore stock quantities: no stock is edited.
    fields = {field.attname: field.value_from_object(product) for field in product._meta.concrete_fields}
    fields['main_image'] = str(product.main_image or '')
    fields['collections'] = sorted(item.pk for item in product.collections.all())
    fields['variant_prices'] = sorted((item.pk, str(item.price) if item.price is not None else None) for item in product.stock_variants.all())
    raw = json.dumps(fields, sort_keys=True, separators=(',', ':'), cls=DjangoJSONEncoder)
    return hashlib.sha256(raw.encode()).hexdigest()


def _snapshots(products):
    return [[item.pk, _fingerprint(item)] for item in products]


def selection_snapshot(actor, products):
    _authorize(actor)
    return signing.dumps({'version': 1, 'actor': actor.pk, 'products': _snapshots(products)}, salt=SELECTION_SALT, compress=True)


def _read(token, salt, actor):
    _authorize(actor)
    if not isinstance(token, str) or len(token) > 150000:
        raise CatalogBulkConflict('Cette préparation est invalide. Recommencez depuis le catalogue.')
    try:
        payload = signing.loads(token, salt=salt, max_age=MAX_AGE)
    except signing.BadSignature as exc:
        raise CatalogBulkConflict('Cette préparation a expiré ou a été modifiée. Recommencez depuis le catalogue.') from exc
    if not isinstance(payload, dict) or payload.get('version') != 1 or payload.get('actor') != actor.pk:
        raise CatalogBulkConflict('Cette préparation ne correspond pas à votre session. Recommencez depuis le catalogue.')
    rows = payload.get('products')
    if not isinstance(rows, list) or any(not isinstance(row, list) or len(row) != 2 or not isinstance(row[1], str) for row in rows):
        raise CatalogBulkConflict('Cette préparation est invalide. Recommencez depuis le catalogue.')
    _ids([row[0] for row in rows])
    return payload


def _check_snapshot(products, payload):
    if _snapshots(products) != payload['products']:
        raise CatalogBulkConflict('Un article a changé depuis votre sélection. Aucun changement n’a été appliqué. Recommencez depuis le catalogue pour vérifier les informations à jour.')


def _validated_changes(changes):
    allowed = set(CatalogBulkForm.OPTIONAL_FIELDS) | {'collections_add', 'collections_remove'}
    if not isinstance(changes, dict) or set(changes) - allowed:
        raise ValidationError('Les changements demandés sont invalides.')
    for key in ('is_published', 'is_featured'):
        if key in changes and type(changes[key]) is not bool:
            raise ValidationError('Choisissez une visibilité valide.')
    form = CatalogBulkForm(changes_form_data(changes))
    if not form.is_valid():
        raise ValidationError([str(message) for errors in form.errors.values() for message in errors])
    return form.changes()


def _category_state(changes):
    ids = set(changes.get('collections_add', [])) | set(changes.get('collections_remove', []))
    if 'category' in changes:
        ids.add(changes['category'])
    result = list(Category.objects.filter(pk__in=ids).order_by('pk').values_list('pk', 'name', 'slug'))
    if len(result) != len(ids):
        raise CatalogBulkConflict('Une rubrique n’existe plus. Recommencez depuis le catalogue.')
    return [list(row) for row in result]


def _validate_publication(product, changes):
    if not changes.get('is_published', product.is_published):
        return
    missing = []
    if not product.description.strip():
        missing.append('une description')
    if not product.main_image and not product.static_image:
        missing.append('une photo principale')
    if not changes.get('category', product.category_id):
        missing.append('une rubrique principale')
    price = Decimal(changes.get('price', str(product.price)))
    if not price.is_finite() or price <= 0:
        missing.append('un prix supérieur à zéro')
    if missing:
        raise ValidationError(f'« {product.name} » : ajoutez {", ".join(missing)} avant de publier ou de conserver cet article en ligne.')


def _label(name, value, categories):
    if name == 'category':
        return categories.get(value, 'Sans rubrique')
    if name == 'price':
        return f'{Decimal(value):.2f} €'.replace('.', ',')
    if name == 'is_published':
        return 'Publié' if value else 'Brouillon / retiré'
    if name == 'is_featured':
        return 'À la une' if value else 'Sans mise en avant'
    if name == 'article_type':
        return str(dict(Product._meta.get_field(name).choices).get(value, 'Classement automatique'))
    if name == 'availability':
        return str(dict(Product._meta.get_field(name).choices).get(value, value))
    return value or 'Non renseignée'


LABELS = {'category': 'Rubrique principale', 'brand': 'Marque', 'article_type': 'Type d’article', 'is_published': 'Visibilité', 'is_featured': 'Mise en avant', 'availability': 'Disponibilité indicative', 'price': 'Prix général'}


def _preview_rows(products, changes):
    categories = dict(Category.objects.values_list('pk', 'name'))
    rows = []
    errors = []
    for product in products:
        try:
            _validate_publication(product, changes)
        except ValidationError as exc:
            errors.extend(exc.messages)
        details = []
        for name in CatalogBulkForm.OPTIONAL_FIELDS:
            if name not in changes:
                continue
            before = product.category_id if name == 'category' else getattr(product, name)
            after = changes[name]
            comparable_after = Decimal(after) if name == 'price' else after
            details.append({'label': LABELS[name], 'before': _label(name, before, categories), 'after': _label(name, after, categories), 'changed': before != comparable_after})
        before_ids = {item.pk for item in product.collections.all()}
        after_ids = (before_ids | set(changes.get('collections_add', []))) - set(changes.get('collections_remove', []))
        if 'collections_add' in changes or 'collections_remove' in changes:
            details.append({'label': 'Rubriques complémentaires', 'before': ', '.join(sorted(categories[item] for item in before_ids)) or 'Aucune', 'after': ', '.join(sorted(categories[item] for item in after_ids)) or 'Aucune', 'changed': before_ids != after_ids})
        rows.append({'product': product, 'details': details, 'changed': any(item['changed'] for item in details), 'specific_prices': sum(item.price is not None for item in product.stock_variants.all())})
    if errors:
        raise ValidationError(errors)
    return rows


def prepare_bulk_preview(actor, ids, changes, snapshot):
    _authorize(actor)
    ids = _ids(ids)
    products = _products(ids)
    payload = _read(snapshot, SELECTION_SALT, actor)
    _check_snapshot(products, payload)
    changes = _validated_changes(changes)
    rows = _preview_rows(products, changes)
    confirmation = signing.dumps({'version': 1, 'actor': actor.pk, 'products': payload['products'], 'changes': changes, 'categories': _category_state(changes)}, salt=CONFIRMATION_SALT, compress=True)
    return rows, confirmation


@transaction.atomic
def apply_bulk_confirmation(actor, token):
    _authorize(actor)
    payload = _read(token, CONFIRMATION_SALT, actor)
    ids = _ids([row[0] for row in payload['products']])
    products = _products(ids, lock=True)
    _check_snapshot(products, payload)
    changes = _validated_changes(payload.get('changes'))
    if _category_state(changes) != payload.get('categories'):
        raise CatalogBulkConflict('Une rubrique a changé depuis l’aperçu. Aucun changement n’a été appliqué. Recommencez depuis le catalogue.')
    rows = _preview_rows(products, changes)
    changed = 0
    for row in rows:
        if not row['changed']:
            continue
        product = row['product']
        update_fields = []
        for name in CatalogBulkForm.OPTIONAL_FIELDS:
            if name not in changes:
                continue
            value = Decimal(changes[name]) if name == 'price' else changes[name]
            attribute = 'category_id' if name == 'category' else name
            if getattr(product, attribute) != value:
                setattr(product, attribute, value)
                update_fields.append(name)
        if update_fields:
            # Product.save records the regular-price history, unlike QuerySet.update.
            product.save(update_fields=update_fields)
        if changes.get('collections_add'):
            product.collections.add(*changes['collections_add'])
        if changes.get('collections_remove'):
            product.collections.remove(*changes['collections_remove'])
        details = [f"{item['label']} : {item['before']} → {item['after']}" for item in row['details'] if item['changed']]
        LogEntry.objects.log_actions(user_id=actor.pk, queryset=Product.objects.filter(pk=product.pk), action_flag=CHANGE, change_message='Modification groupée. ' + ' ; '.join(details))
        changed += 1
    return changed


@catalog_required
@require_http_methods(['GET', 'POST'])
def catalog_bulk(request):
    from .catalog_navigation import catalog_return
    from django.urls import reverse

    return_to = catalog_return(request)
    if request.method == 'GET':
        return redirect(return_to or reverse('core:catalog_list'))
    action = request.POST.get('action', 'start')
    context = {'step': 'edit', 'form': CatalogBulkForm(), 'products': [], 'selection_snapshot': '', 'price_changed': False, 'return_to': return_to}
    try:
        if action == 'apply':
            token = request.POST.get('confirmation', '')
            payload = _read(token, CONFIRMATION_SALT, request.user)
            count = apply_bulk_confirmation(request.user, token)
            request.session['catalogue_applied_products'] = [str(row[0]) for row in payload['products']]
            messages.success(request, f'{count} produit(s) mis à jour.' if count else 'Les articles correspondaient déjà aux changements choisis. Aucune modification nécessaire.')
            return redirect(return_to or reverse('core:catalog_list'))
        if action == 'back':
            payload = _read(request.POST.get('confirmation', ''), CONFIRMATION_SALT, request.user)
            ids = _ids([row[0] for row in payload['products']])
            products = _products(ids)
            _check_snapshot(products, payload)
            changes = _validated_changes(payload.get('changes'))
            context.update(products=products, selection_snapshot=selection_snapshot(request.user, products), form=CatalogBulkForm(initial=changes_form_data(changes)))
        elif action in ('start', 'preview'):
            ids = _ids(request.POST.getlist('selected_products'))
            products = _products(ids)
            context['products'] = products
            if action == 'start':
                context['selection_snapshot'] = selection_snapshot(request.user, products)
            else:
                context['selection_snapshot'] = request.POST.get('selection_snapshot', '')
                form = CatalogBulkForm(request.POST)
                context['form'] = form
                if form.is_valid():
                    changes = form.changes()
                    rows, confirmation = prepare_bulk_preview(request.user, ids, changes, context['selection_snapshot'])
                    context.update(step='preview', rows=rows, confirmation=confirmation, price_changed='price' in changes, changed_count=sum(row['changed'] for row in rows))
                else:
                    return render(request, 'core/catalog/bulk.html', context, status=400)
        else:
            raise ValidationError('Cette action n’est pas disponible. Revenez au catalogue.')
    except CatalogBulkConflict as exc:
        context.update(step='error', errors=exc.messages)
        return render(request, 'core/catalog/bulk.html', context, status=409)
    except ValidationError as exc:
        context['errors'] = exc.messages
        if not context['products']:
            context['step'] = 'error'
        return render(request, 'core/catalog/bulk.html', context, status=400)
    return render(request, 'core/catalog/bulk.html', context)
