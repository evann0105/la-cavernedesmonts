"""Create an independent, unpublished product from an explicitly confirmed source."""
import hashlib
import json
import logging
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlencode
from uuid import uuid4

from django import forms
from django.contrib import messages
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.contrib.staticfiles import finders
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.files.base import File
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import translation
from django.views.decorators.http import require_http_methods

from connexion.email_verification import email_access_allowed
from stocks.models import Variant
from .catalog_access import can_manage_catalog, catalog_required
from .catalog_navigation import catalog_return
from .models import Product, ProductColor, ProductImage
from .product_images import imported_gallery_urls

SALT = 'catalogue-duplication-v1'
MAX_AGE = 24 * 60 * 60
ARCHIVED_MESSAGE = 'Cette fiche est un doublon archivé. Dupliquez la fiche conservée pour reprendre les informations à jour.'
logger = logging.getLogger(__name__)


class DuplicationError(ValueError):
    pass


class DuplicationConflict(DuplicationError):
    pass


@dataclass(frozen=True)
class DuplicationResult:
    product: Product
    reused: bool = False


class DuplicationForm(forms.Form):
    snapshot = forms.CharField(max_length=2000, widget=forms.HiddenInput)


def _authorized(actor):
    if actor is None or not can_manage_catalog(actor) or not email_access_allowed(actor):
        raise PermissionDenied


def _check_source(source):
    if source.duplicate_of_id is not None:
        raise DuplicationConflict(ARCHIVED_MESSAGE)


def _static_path(name):
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or '..' in relative.parts or '\\' in name or relative.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
        raise DuplicationError('Une photo d’origine est introuvable. Remplacez-la sur la fiche avant de la dupliquer.')
    found = finders.find(name)
    if not found or not Path(found).is_file():
        raise DuplicationError('Une photo d’origine est introuvable. Remplacez-la sur la fiche avant de la dupliquer.')
    return Path(found)


def _source_state(source):
    # Include every editable relation, even stock: the confirmation must describe
    # the exact source copied. Normal catalog writes lock the parent row first.
    photos = list(source.images.order_by('order', 'pk'))
    colors = list(source.color_photos.order_by('pk'))
    variants = list(source.stock_variants.order_by('pk'))
    collections = list(source.collections.order_by('pk').values_list('pk', flat=True))
    initial = ([source.image_url] if source.image_url else []) + [photo.image.url for photo in photos]
    imported = imported_gallery_urls(source, initial)[len(initial):]
    prefix = static('')
    imported_names = []
    for url in imported:
        if not url.startswith(prefix):
            raise DuplicationError('Une photo d’origine ne peut pas être copiée. Vérifiez la galerie du produit.')
        imported_names.append(unquote(url[len(prefix):]))
    static_names = ([source.static_image] if source.static_image and not source.main_image else []) + imported_names
    static_fingerprints = []
    for name in static_names:
        path = _static_path(name)
        stat = path.stat()
        static_fingerprints.append([name, stat.st_size, stat.st_mtime_ns])
    if len(photos) + len(imported_names) > 50:
        raise DuplicationError('Cette fiche dépasse 50 photos de galerie. Réduisez sa galerie avant de la dupliquer.')

    def values(instance):
        result = {}
        for field in instance._meta.concrete_fields:
            value = getattr(instance, field.attname)
            result[field.attname] = value.name if hasattr(value, 'storage') else value
        return result

    payload = {'product': values(source), 'photos': [values(photo) for photo in photos],
               'colors': [values(color) for color in colors], 'variants': [values(variant) for variant in variants],
               'collections': collections, 'static_files': static_fingerprints}
    digest = hashlib.sha256(json.dumps(payload, cls=DjangoJSONEncoder, sort_keys=True).encode()).hexdigest()
    return {'digest': digest, 'photos': photos, 'colors': colors, 'variants': variants,
            'collections': collections, 'imported_names': imported_names}


def duplication_snapshot(source, *, actor):
    _authorized(actor)
    _check_source(source)
    state = _source_state(source)
    token = signing.dumps({'source': source.pk, 'actor': actor.pk, 'digest': state['digest'], 'nonce': uuid4().hex}, salt=SALT)
    return token, state


def _confirmed(snapshot, source_id, actor):
    try:
        payload = signing.loads(snapshot, salt=SALT, max_age=MAX_AGE)
    except (signing.BadSignature, TypeError, ValueError) as exc:
        raise DuplicationError('La confirmation a expiré. Rechargez cette page avant de dupliquer le produit.') from exc
    if (not isinstance(payload, dict) or set(payload) != {'source', 'actor', 'digest', 'nonce'}
            or payload['source'] != source_id or payload['actor'] != actor.pk
            or not isinstance(payload['digest'], str) or len(payload['digest']) != 64
            or not isinstance(payload['nonce'], str) or len(payload['nonce']) != 32):
        raise DuplicationError('Cette confirmation ne correspond pas à votre fiche. Rechargez la page.')
    return payload


def duplicate_product(source_id, *, snapshot, actor):
    """Copy content and image bytes, but never orders, reservations or promotions.

    The source lock serializes repeat submissions. The audit entry is also the
    durable receipt for this signed request, so a retry returns its existing copy.
    """
    _authorized(actor)
    payload = _confirmed(snapshot, source_id, actor)
    receipt = json.dumps([{'added': {'name': 'produit', 'object': 'Copie du produit'},
                           'catalog_duplicate': {'source': source_id, 'request': payload['nonce']}}], sort_keys=True)
    created_files = []
    copied_files = {}
    storage = Product._meta.get_field('main_image').storage

    def save_file(stream, suffix, key):
        if key in copied_files:
            return copied_files[key]
        extension = suffix.lower() if suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp') else '.webp'
        name = storage.save(f'products/copies/{uuid4().hex}{extension}', File(stream))
        created_files.append(name)
        copied_files[key] = name
        return name

    def copy_upload(field):
        key = (id(field.storage), field.name)
        if key in copied_files:
            return copied_files[key]
        with field.storage.open(field.name, 'rb') as stream:
            return save_file(stream, Path(field.name).suffix, key)

    def copy_static(name):
        path = _static_path(name)
        with path.open('rb') as stream:
            return save_file(stream, path.suffix, ('static', name))

    try:
        with transaction.atomic():
            if connection.vendor == 'sqlite':
                Product.objects.filter(pk=source_id).update(is_featured=F('is_featured'))
            source = Product.objects.select_for_update().get(pk=source_id)
            _check_source(source)
            content_type = ContentType.objects.get_for_model(Product)
            previous = LogEntry.objects.filter(user_id=actor.pk, content_type=content_type,
                                              action_flag=ADDITION, change_message=receipt).first()
            if previous:
                copy = Product.objects.filter(pk=previous.object_id).first()
                if copy is None:
                    raise DuplicationConflict('Cette copie a été retirée. Rechargez la fiche pour en créer une nouvelle.')
                return DuplicationResult(copy, reused=True)
            list(source.stock_variants.select_for_update().order_by('pk'))
            state = _source_state(source)
            if state['digest'] != payload['digest']:
                raise DuplicationConflict('Cette fiche a été modifiée depuis votre confirmation. Rechargez la page pour copier sa dernière version.')
            fields = ('category_id', 'price', 'description', 'brand', 'model_reference', 'composition',
                      'brand_contact', 'information_review_notes', 'benefits', 'translations',
                      'main_color', 'sizes', 'article_type')
            values = {field: deepcopy(getattr(source, field)) for field in fields}
            # Reviewed legacy benefits are keyed by slug; materialize them before
            # giving the copy a new identity. No imported URL/alias follows it.
            if values['benefits'] is None:
                with translation.override('fr'):
                    values['benefits'] = '\n'.join(source.benefit_lines)
            for localized in values['translations'].values():
                if isinstance(localized, dict) and localized.get('name'):
                    localized['name'] = ('Copie de ' + localized['name'])[:200]
            values['information_sources'] = {key: deepcopy(value) for key, value in (source.information_sources or {}).items() if key != '_duplicate_review'}
            copy = Product(name=('Copie de ' + source.name)[:200], is_published=False,
                           is_featured=False, availability='unknown', include_imported_gallery=False,
                           source_url=None, duplicate_of=None, static_image='', **values)
            if source.main_image:
                copy.main_image = copy_upload(source.main_image)
            elif source.static_image:
                copy.main_image = copy_static(source.static_image)
            copy.save()
            copy.collections.set(state['collections'])
            for photo in state['photos']:
                ProductImage.objects.create(product=copy, image=copy_upload(photo.image), alt=photo.alt, order=photo.order)
            next_order = max((photo.order for photo in state['photos']), default=0) + 1
            for index, name in enumerate(state['imported_names']):
                ProductImage.objects.create(product=copy, image=copy_static(name), alt=source.name, order=next_order + index)
            for color in state['colors']:
                ProductColor.objects.create(product=copy, name=color.name, image=copy_upload(color.image))
            for variant in state['variants']:
                Variant.objects.create(product=copy, size=variant.size, color=variant.color, price=variant.price,
                                       reference='', quantity=0, reserved=0, revision=1)
            LogEntry.objects.log_actions(user_id=actor.pk, queryset=Product.objects.filter(pk=copy.pk),
                                         action_flag=ADDITION, change_message=receipt)
            return DuplicationResult(copy)
    except Exception as exc:
        # File storage is outside the database transaction. Only new copy paths
        # are removed on failure; source and other product media stay untouched.
        for name in created_files:
            try:
                storage.delete(name)
            except OSError:
                logger.warning('Impossible de nettoyer une photo de copie abandonnée.', exc_info=True)
        if isinstance(exc, OSError):
            raise DuplicationError('Une photo n’a pas pu être copiée. Vérifiez les photos de la fiche puis réessayez ; aucun produit n’a été créé.') from exc
        raise


@catalog_required
@require_http_methods(['GET', 'POST'])
def catalog_duplicate(request, pk):
    request.catalog_return_to = return_to = catalog_return(request)
    source = get_object_or_404(Product, pk=pk)
    state = None
    status = 200
    if source.duplicate_of_id is not None:
        form = DuplicationForm(data={'snapshot': 'unavailable'})
        form.add_error(None, ARCHIVED_MESSAGE)
        status = 409
    elif request.method == 'POST':
        form = DuplicationForm(request.POST)
        if form.is_valid():
            try:
                result = duplicate_product(pk, snapshot=form.cleaned_data['snapshot'], actor=request.user)
            except DuplicationError as exc:
                source.refresh_from_db()
                form.add_error(None, str(exc))
                status = 409 if isinstance(exc, DuplicationConflict) else 400
            else:
                messages.success(request, 'La copie existe déjà : vous pouvez continuer à la compléter.' if result.reused else 'La fiche a été copiée en brouillon. Vérifiez les informations et renseignez le stock avant de la publier.')
                target = reverse('core:catalog_edit', kwargs={'pk': result.product.pk})
                if return_to:
                    target += '?' + urlencode({'return_to': return_to})
                return redirect(target)
        else:
            status = 400
    else:
        form = DuplicationForm()
        try:
            snapshot, state = duplication_snapshot(source, actor=request.user)
        except (DuplicationError, OSError) as exc:
            form = DuplicationForm(data={'snapshot': 'unavailable'})
            form.add_error(None, str(exc) if isinstance(exc, DuplicationError) else 'Une photo est introuvable. Vérifiez la galerie avant de dupliquer ce produit.')
            status = 400
        else:
            form.initial['snapshot'] = snapshot
    return render(request, 'core/catalog/duplicate.html', {'product': source, 'form': form,
                  'variant_count': len(state['variants']) if state else source.stock_variants.count(),
                  'gallery_count': len(state['photos']) + len(state['imported_names']) if state else source.images.count(),
                  'color_count': len(state['colors']) if state else source.color_photos.count()}, status=status)
