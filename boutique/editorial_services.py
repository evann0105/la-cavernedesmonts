"""Authorized and versioned saves, including validation outside HTTP views."""
import re
from copy import deepcopy
from datetime import datetime

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.files.base import File
from django.db import IntegrityError, connection, transaction
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
        _faq_write_lock()
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
        if instance.archived_at is not None:
            raise EditorialConflict('Cette annonce a été archivée. Restaurez-la depuis les archives avant de la modifier.')
        for name in ('name', 'kind', 'message', 'starts_at', 'ends_at', 'enabled', 'priority', 'target', 'link_label'):
            setattr(instance, name, validated[name])
        return _persist(instance, is_new, actor, 'Annonce du site')


FAQ_ORDER_SALT = 'boutique.editorial.faq-order.v1'
FAQ_WRITE_LOCK = 1128552006


def _faq_write_lock():
    """Serialize all FAQ writers, including inserts when the collection is empty."""
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [FAQ_WRITE_LOCK])
        elif connection.vendor == 'sqlite':
            # The legacy SQLite migration environment has no advisory locks;
            # acquire its transaction-wide writer lock without changing rows.
            table = connection.ops.quote_name(FAQEntry._meta.db_table)
            cursor.execute(f'UPDATE {table} SET position = position WHERE 1 = 0')
        else:
            raise EditorialError('La réorganisation nécessite une base compatible avec le verrouillage des questions.')


def faq_order_snapshot(entries, actor):
    _authorized(actor)
    return signing.dumps({'actor': actor.pk, 'entries': [[entry.pk, entry.revision] for entry in entries]},
                         salt=FAQ_ORDER_SALT, compress=True)


def _faq_snapshot(token, actor):
    if not isinstance(token, str) or len(token) > 60000:
        raise EditorialError('Rechargez la liste des questions avant de continuer.')
    try:
        payload = signing.loads(token, salt=FAQ_ORDER_SALT, max_age=86400)
    except signing.SignatureExpired as exc:
        raise EditorialConflict('Cette liste a expiré. Rechargez-la avant de modifier son ordre.') from exc
    except signing.BadSignature as exc:
        raise EditorialError('La liste des questions n’a pas pu être vérifiée. Rechargez-la.') from exc
    if not isinstance(payload, dict) or set(payload) != {'actor', 'entries'} or payload['actor'] != actor.pk:
        raise EditorialError('Cette liste ne correspond pas à votre session. Rechargez-la.')
    entries = payload['entries']
    if (not isinstance(entries, list) or len(entries) > 1000 or any(
            not isinstance(row, list) or len(row) != 2 or
            any(type(value) is not int or value <= 0 for value in row) for row in entries)):
        raise EditorialError('La liste des questions est invalide. Rechargez-la.')
    revisions = {pk: revision for pk, revision in entries}
    if len(revisions) != len(entries):
        raise EditorialError('La liste des questions est invalide. Rechargez-la.')
    return revisions


def reorder_faq(order, snapshot, actor):
    _authorized(actor)
    if (not isinstance(order, list) or len(order) > 1000 or
            any(type(pk) is not int or pk <= 0 for pk in order) or len(set(order)) != len(order)):
        raise EditorialError('Chaque question doit apparaître une seule fois dans la liste.')
    expected = _faq_snapshot(snapshot, actor)
    if set(order) != set(expected):
        raise EditorialError('La liste doit contenir toutes les questions, une seule fois chacune.')
    with transaction.atomic():
        _faq_write_lock()
        entries = list(FAQEntry.objects.select_for_update().order_by('pk'))
        if {entry.pk: entry.revision for entry in entries} != expected:
            raise EditorialConflict('Les questions ont changé dans un autre onglet. Rechargez la liste avant de réorganiser.')
        by_id = {entry.pk: entry for entry in entries}
        result = []
        for position, pk in enumerate(order, 1):
            entry = by_id[pk]
            if entry.position != position:
                entry.position = position
                _persist(entry, False, actor, 'Ordre des questions fréquentes')
            result.append(entry)
        return result


def set_faq_visibility(pk, revision, enabled, actor):
    _authorized(actor)
    expected = _revision({'revision': revision})
    if type(enabled) is not bool:
        raise EditorialError('Choisissez d’afficher ou de masquer la question.')
    if type(pk) is not int or pk <= 0:
        raise EditorialError('Cette question n’existe pas.')
    with transaction.atomic():
        _faq_write_lock()
        entry, _ = _locked_entry(FAQEntry, pk, expected)
        if entry.enabled != enabled:
            entry.enabled = enabled
            _persist(entry, False, actor, 'Question fréquente ' + ('affichée' if enabled else 'masquée'))
        return entry


def _existing_announcement(pk, revision):
    expected = _revision({'revision': revision})
    if type(pk) is not int or pk <= 0:
        raise EditorialError('Cette annonce n’existe pas.')
    entry, _ = _locked_entry(SiteAnnouncement, pk, expected)
    return entry


def duplicate_announcement(pk, revision, actor):
    _authorized(actor)
    with transaction.atomic():
        source = _existing_announcement(pk, revision)
        names = ('kind', 'message', 'starts_at', 'ends_at', 'priority', 'target', 'link_label')
        copied = SiteAnnouncement(**{name: getattr(source, name) for name in names},
            name=('Copie de ' + source.name)[:120], enabled=False, archived_at=None)
        return _persist(copied, True, actor, 'Annonce dupliquée en brouillon')


def archive_announcement(pk, revision, actor):
    _authorized(actor)
    with transaction.atomic():
        entry = _existing_announcement(pk, revision)
        if entry.archived_at is not None:
            raise EditorialError('Cette annonce est déjà archivée.')
        entry.enabled = False
        entry.archived_at = timezone.now()
        return _persist(entry, False, actor, 'Annonce archivée')


def restore_announcement(pk, revision, actor):
    _authorized(actor)
    with transaction.atomic():
        entry = _existing_announcement(pk, revision)
        if entry.archived_at is None:
            raise EditorialError('Cette annonce n’est pas archivée.')
        entry.archived_at = None
        entry.enabled = False
        return _persist(entry, False, actor, 'Annonce restaurée en brouillon')


def pause_announcement(pk, revision, actor):
    _authorized(actor)
    with transaction.atomic():
        entry = _existing_announcement(pk, revision)
        if entry.archived_at is not None:
            raise EditorialConflict('Cette annonce a été archivée. Restaurez-la avant de la modifier.')
        if entry.enabled:
            entry.enabled = False
            _persist(entry, False, actor, 'Annonce mise en pause')
        return entry


def move_faq(pk, direction, snapshot, actor):
    _authorized(actor)
    if type(pk) is not int or pk <= 0 or direction not in ('up', 'down'):
        raise EditorialError('Choisissez de monter ou de descendre une question de la liste.')
    # Dict insertion order preserves the exact signed display order. Reordering
    # below rechecks all revisions and membership under the shared FAQ lock.
    order = list(_faq_snapshot(snapshot, actor))
    if pk not in order:
        raise EditorialError('Cette question n’appartient pas à la liste affichée. Rechargez-la.')
    position = order.index(pk)
    destination = position + (-1 if direction == 'up' else 1)
    if destination < 0 or destination >= len(order):
        raise EditorialError('Cette question est déjà à cette extrémité de la liste.')
    order[position], order[destination] = order[destination], order[position]
    return reorder_faq(order, snapshot, actor)
