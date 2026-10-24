"""Authorized and versioned saves, including validation outside HTTP views."""
from datetime import datetime

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.core.exceptions import PermissionDenied
from django.core.files.base import File
from django.db import IntegrityError, transaction
from django.db.models.fields.files import FieldFile
from django.utils import timezone

from connexion.email_verification import email_access_allowed
from core.catalog_access import can_manage_catalog
from .editorial_configuration import page_content
from .editorial_forms import AnnouncementForm, EditorialImageInitial, FAQForm, PageContentForm
from .editorial_schema import PAGE_SPECS
from .models import ContentPage, FAQEntry, SiteAnnouncement


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


def _cleanup_image(instance, previous_name):
    image = getattr(instance, 'image', None)
    if (image and image.name != previous_name and image.name.startswith('editorial/') and image._committed):
        # Old files are intentionally retained: a backup or another reader may
        # still refer to them. Only this failed save's new file is removed.
        image.storage.delete(image.name)


def save_page(key, cleaned_data, actor):
    _authorized(actor)
    expected = _revision(cleaned_data)
    if not isinstance(key, str) or key not in PAGE_SPECS:
        raise EditorialError('Cette page n’existe pas.')
    _typed(cleaned_data, strings=tuple(PAGE_SPECS[key]['fields']), extra=('image',))
    instance, previous_name, uploaded = None, '', False
    try:
        with transaction.atomic():
            instance = ContentPage.objects.select_for_update().filter(key=key).first()
            revision = instance.revision if instance is not None else 0
            if expected != revision:
                raise EditorialConflict(CONFLICT_MESSAGE)
            is_new = instance is None
            instance = instance or ContentPage(key=key)
            previous_name = instance.image.name
            values = {name: cleaned_data[name] for name in PAGE_SPECS[key]['fields']}
            values['revision'] = expected
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
            validated = _validate(PageContentForm(key, values, files, current=page_content(key, instance=instance)))
            instance.data = {name: validated[name] for name in PAGE_SPECS[key]['fields']}
            if validated['image'] is False:
                instance.image = ''
            elif isinstance(validated['image'], File) and not isinstance(validated['image'], FieldFile):
                instance.image = validated['image']
                uploaded = True
            return _persist(instance, is_new, actor, PAGE_SPECS[key]['title'])
    except Exception:
        if uploaded:
            _cleanup_image(instance, previous_name)
        raise


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
