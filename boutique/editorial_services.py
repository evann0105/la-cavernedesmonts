"""Authorized and versioned saves, including validation outside HTTP views."""
import re
from copy import deepcopy
from datetime import datetime

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.core.exceptions import PermissionDenied
from django.core.files.base import File
from django.db import IntegrityError, transaction
from django.db.models.fields.files import FieldFile
from django.utils import timezone

from connexion.email_verification import email_access_allowed
from core.catalog_access import can_manage_catalog
from .editorial_configuration import page_content, page_draft_content
from .editorial_forms import AnnouncementForm, EditorialImageInitial, FAQForm, PageContentForm
from .editorial_schema import PAGE_SPECS, validate_page_data
from .models import ContentPage, ContentPageVersion, FAQEntry, SiteAnnouncement


class EditorialError(ValueError):
    pass


class EditorialConflict(EditorialError):
    pass


CONFLICT_MESSAGE = 'Ces informations ont été modifiées dans un autre onglet. Rechargez la page avant de les enregistrer à nouveau.'


def _authorized(actor):
    if actor is None or not can_manage_catalog(actor) or not email_access_allowed(actor):
        raise PermissionDenied


def _revision(data):
    if not isinstance(data, dict) or type(data.get('revision')) is not int or data['revision'] < 0:
        raise EditorialError('Rechargez la page pour vérifier sa version actuelle.')
    return data['revision']


def _validate(form):
    if not form.is_valid():
        errors = []
        for name, messages in form.errors.items():
            label = form.fields[name].label if name in form.fields else ''
            errors.extend((str(label) + ' : ' if label else '') + str(message) for message in messages)
        raise EditorialError('Vérifiez les informations. ' + ' '.join(errors))
    return form.cleaned_data


def _typed(data, *, strings, integers=(), booleans=(), dates=(), extra=()):
    allowed = {'revision', *strings, *integers, *booleans, *dates, *extra}
    if set(data) - allowed:
        raise EditorialError('Une information ne correspond pas à cette page. Rechargez-la avant de continuer.')
    if any(not isinstance(data.get(name), str) for name in strings):
        raise EditorialError('Renseignez les textes et les destinations sous forme de texte.')
    if any(type(data.get(name)) is not int for name in integers):
        raise EditorialError('Choisissez un numéro valide dans les champs prévus.')
    if any(type(data.get(name)) is not bool for name in booleans):
        raise EditorialError('Utilisez les cases prévues pour activer ou masquer le contenu.')
    if any(not isinstance(data.get(name), datetime) or timezone.is_naive(data[name]) for name in dates):
        raise EditorialError('Renseignez des dates et heures valides avec leur fuseau horaire.')


def _persist(instance, is_new, actor, label):
    instance.revision = (0 if is_new else instance.revision) + 1
    instance.updated_by = actor
    instance.full_clean(validate_unique=False, validate_constraints=False)
    try:
        with transaction.atomic():
            instance.save(force_insert=is_new)
    except IntegrityError as exc:
        if is_new and isinstance(instance, ContentPage):
            raise EditorialConflict(CONFLICT_MESSAGE) from exc
        raise
    LogEntry.objects.log_actions(user_id=actor.pk,
        queryset=type(instance).objects.filter(pk=instance.pk),
        action_flag=ADDITION if is_new else CHANGE,
        change_message='Pages et annonces : ' + label + '.')
    return instance


def _cleanup_image(instance, previous_names, field_name):
    image = getattr(instance, field_name, None)
    if (image and image.name not in previous_names and image.name.startswith('editorial/') and image._committed):
        # Publication history and other readers keep their old immutable paths.
        image.storage.delete(image.name)


def _page_key(key):
    if not isinstance(key, str) or key not in PAGE_SPECS:
        raise EditorialError('Cette page n’existe pas.')


def _locked_page(key, expected):
    instance = ContentPage.objects.select_for_update().filter(key=key).first()
    if expected != (instance.revision if instance is not None else 0):
        raise EditorialConflict(CONFLICT_MESSAGE)
    return instance or ContentPage(key=key), instance is None


def _baseline(instance, is_new):
    if not is_new and instance.versions.exists():
        return None
    return {'publication_revision': 0 if is_new else instance.revision,
        'data': deepcopy(page_content(instance.key, instance=instance)['values']),
        'image': instance.image.name or '',
        'created_at': instance.updated_at or timezone.now(),
        'created_by': instance.updated_by}


def _record_versions(instance, baseline, actor, *, published):
    if baseline is not None:
        ContentPageVersion.objects.create(page=instance, **baseline)
    if published:
        ContentPageVersion.objects.create(page=instance, publication_revision=instance.revision,
            data=deepcopy(instance.data), image=instance.image.name or '', created_by=actor)


def _validated_page(key, cleaned_data, current, previous_name):
    values = {name: cleaned_data[name] for name in PAGE_SPECS[key]['fields']}
    values['revision'] = cleaned_data['revision']
    files = {}
    image = cleaned_data.get('image')
    if image is False:
        values['image-clear'] = True
    elif image is None or isinstance(image, EditorialImageInitial):
        pass
    elif isinstance(image, FieldFile):
        if image.name != previous_name:
            raise EditorialError('Ajoutez la photo depuis le sélecteur de fichiers.')
    elif isinstance(image, File):
        files['image'] = image
    else:
        raise EditorialError('Choisissez une photo JPEG, PNG ou WebP valide.')
    return _validate(PageContentForm(key, values, files, current=current))


def _available_photo(image):
    name = image.name or ''
    if name and (not re.fullmatch(r'editorial/[a-f0-9]{32}\.webp', name) or not image.storage.exists(name)):
        raise EditorialError('La photo de cette version n’est plus disponible. Ajoutez une nouvelle photo dans le brouillon avant de publier.')


def save_page(key, cleaned_data, actor, *, publish=True):
    """Save the editor snapshot; publishing is kept as the legacy service default."""
    _authorized(actor)
    expected = _revision(cleaned_data)
    _page_key(key)
    if type(publish) is not bool:
        raise EditorialError('Choisissez de préparer un brouillon ou de publier la page.')
    _typed(cleaned_data, strings=tuple(PAGE_SPECS[key]['fields']), extra=('image',))
    instance, previous_names, uploaded = None, set(), False
    field_name = 'image' if publish else 'draft_image'
    try:
        with transaction.atomic():
            instance, is_new = _locked_page(key, expected)
            baseline = _baseline(instance, is_new)
            if is_new:
                instance.data = deepcopy(baseline['data'])
            previous_names = {instance.image.name, instance.draft_image.name}
            editing_image = instance.draft_image if instance.has_draft else instance.image
            previous_name = editing_image.name or ''
            current = page_draft_content(key, instance=instance)
            validated = _validated_page(key, cleaned_data, current, previous_name)
            data = {name: validated[name] for name in PAGE_SPECS[key]['fields']}
            photo = previous_name
            if validated['image'] is False:
                photo = ''
            elif isinstance(validated['image'], File) and not isinstance(validated['image'], FieldFile):
                photo = validated['image']
                uploaded = True
            if publish:
                instance.data = data
                instance.image = photo
                instance.draft_data = None
                instance.draft_image = ''
                instance.published_revision = expected + 1
            else:
                instance.draft_data = data
                instance.draft_image = photo
                if baseline is not None:
                    instance.published_revision = baseline['publication_revision']
            if publish and not uploaded:
                _available_photo(instance.image)
            _persist(instance, is_new, actor, PAGE_SPECS[key]['title'] + (' publiée' if publish else ' · brouillon enregistré'))
            _record_versions(instance, baseline, actor, published=publish)
            return instance
    except Exception:
        if uploaded:
            _cleanup_image(instance, previous_names, field_name)
        raise


def publish_page(key, revision, actor):
    _authorized(actor)
    expected = _revision({'revision': revision})
    _page_key(key)
    with transaction.atomic():
        instance, is_new = _locked_page(key, expected)
        if is_new or not instance.has_draft:
            raise EditorialError('Il n’y a pas de brouillon à publier pour cette page.')
        baseline = _baseline(instance, is_new)
        validate_page_data(key, instance.draft_data)
        _available_photo(instance.draft_image)
        current = page_draft_content(key, instance=instance)
        # Validate stored data again, even if invoked outside the HTTP editor.
        validated = _validated_page(key, {**current['values'], 'revision': expected, 'image': None},
                                    current, instance.draft_image.name)
        instance.data = {name: validated[name] for name in PAGE_SPECS[key]['fields']}
        instance.image = instance.draft_image.name or ''
        instance.draft_data = None
        instance.draft_image = ''
        instance.published_revision = expected + 1
        _persist(instance, False, actor, PAGE_SPECS[key]['title'] + ' publiée')
        _record_versions(instance, baseline, actor, published=True)
        return instance


def restore_page_version(key, pk, revision, actor):
    _authorized(actor)
    expected = _revision({'revision': revision})
    _page_key(key)
    if type(pk) is not int or pk <= 0:
        raise EditorialError('Cette version n’existe pas.')
    with transaction.atomic():
        instance, is_new = _locked_page(key, expected)
        if is_new:
            raise EditorialError('Cette version n’existe pas.')
        version = ContentPageVersion.objects.filter(pk=pk, page=instance).first()
        if version is None:
            raise EditorialError('Cette version n’appartient pas à cette page.')
        version.full_clean(validate_unique=False, validate_constraints=False)
        _available_photo(version.image)
        instance.draft_data = deepcopy(version.data)
        instance.draft_image = version.image.name or ''
        _persist(instance, False, actor, PAGE_SPECS[key]['title'] + ' · version restaurée dans le brouillon')
        return instance


def _locked_entry(model, pk, expected):
    if pk is not None and (type(pk) is not int or pk <= 0):
        raise EditorialError('Cette entrée n’existe pas.')
    instance = model.objects.select_for_update().filter(pk=pk).first() if pk is not None else None
    if pk is not None and instance is None:
        raise EditorialConflict('Cette entrée n’existe plus. Rechargez la liste avant de continuer.')
    if expected != (instance.revision if instance is not None else 0):
        raise EditorialConflict(CONFLICT_MESSAGE)
    return instance or model(), instance is None


def save_faq(cleaned_data, actor, pk=None):
    _authorized(actor)
    expected = _revision(cleaned_data)
    _typed(cleaned_data, strings=('question', 'answer', 'target', 'link_label'),
           integers=('position',), booleans=('enabled',))
    validated = _validate(FAQForm(cleaned_data))
    with transaction.atomic():
        instance, is_new = _locked_entry(FAQEntry, pk, expected)
        for name in ('question', 'answer', 'target', 'link_label', 'position', 'enabled'):
            setattr(instance, name, validated[name])
        return _persist(instance, is_new, actor, 'Question fréquente')


def save_announcement(cleaned_data, actor, pk=None):
    _authorized(actor)
    expected = _revision(cleaned_data)
    _typed(cleaned_data, strings=('name', 'kind', 'message', 'target', 'link_label'),
           integers=('priority',), booleans=('enabled',), dates=('starts_at', 'ends_at'))
    validated = _validate(AnnouncementForm(cleaned_data))
    with transaction.atomic():
        instance, is_new = _locked_entry(SiteAnnouncement, pk, expected)
        for name in ('name', 'kind', 'message', 'starts_at', 'ends_at', 'enabled', 'priority', 'target', 'link_label'):
            setattr(instance, name, validated[name])
        return _persist(instance, is_new, actor, 'Annonce du site')
