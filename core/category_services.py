"""Reversible category changes, serialized and checked against the displayed version."""
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import Max
from django.utils.text import slugify

from connexion.email_verification import email_access_allowed
from .catalog_access import can_manage_catalog
from .category_forms import CategoryForm
from .models import Category


class CategoryError(ValueError):
    pass


class CategoryConflict(CategoryError):
    pass


CONFLICT_MESSAGE = 'Les rubriques ont changé dans un autre onglet. Rechargez la page avant de continuer.'
ORDER_SALT = 'core.categories.order.v1'
CATEGORY_WRITE_LOCK = 1128481108


def _authorized(actor):
    if actor is None or not can_manage_catalog(actor) or not email_access_allowed(actor):
        raise PermissionDenied


def _write_lock():
    # The collection lock also protects insertion when no category exists yet.
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [CATEGORY_WRITE_LOCK])
        elif connection.vendor == 'sqlite':
            table = connection.ops.quote_name(Category._meta.db_table)
            cursor.execute(f'UPDATE {table} SET position = position WHERE 1 = 0')
        else:
            raise CategoryError('La gestion des rubriques nécessite une base compatible avec leur verrouillage.')


def _version(value, *, creating=False):
    if type(value) is not int or value < (0 if creating else 1):
        raise CategoryError('Rechargez la rubrique pour vérifier sa version actuelle.')
    return value


def _row(pk, revision):
    if type(pk) is not int or pk < 1:
        raise CategoryError('Cette rubrique n’existe pas.')
    row = Category.objects.select_for_update().filter(pk=pk).first()
    if row is None or row.revision != revision:
        raise CategoryConflict(CONFLICT_MESSAGE)
    return row


def _persist(row, actor, label, *, creating=False):
    row.revision = 1 if creating else row.revision + 1
    row.full_clean()
    row.save(force_insert=creating)
    LogEntry.objects.log_actions(user_id=actor.pk, queryset=Category.objects.filter(pk=row.pk),
        action_flag=ADDITION if creating else CHANGE, change_message='Rubriques et menu : ' + label + '.')
    return row


def save_category(pk, values, actor):
    _authorized(actor)
    creating = pk is None
    if (not isinstance(values, dict) or set(values) != {'name', 'menu_label', 'show_in_menu', 'revision'}
            or not isinstance(values['name'], str) or not isinstance(values['menu_label'], str)
            or type(values['show_in_menu']) is not bool):
        raise CategoryError('Utilisez les champs prévus pour modifier la rubrique.')
    revision = _version(values['revision'], creating=creating)
    if creating and revision != 0:
        raise CategoryConflict(CONFLICT_MESSAGE)
    form = CategoryForm(values)
    if not form.is_valid():
        raise CategoryError('Vérifiez le nom et le libellé de la rubrique. ' + ' '.join(
            str(message) for errors in form.errors.values() for message in errors))
    cleaned = form.cleaned_data
    with transaction.atomic():
        _write_lock()
        row = Category() if creating else _row(pk, revision)
        existing_names = Category.objects.exclude(pk=row.pk).values_list('name', flat=True)
        if any(' '.join(name.split()).casefold() == cleaned['name'].casefold() for name in existing_names):
            raise CategoryError('Une rubrique porte déjà ce nom. Choisissez un autre nom ou modifiez la rubrique existante.')
        if creating:
            base = slugify(cleaned['name'])[:130] or 'rubrique'
            row.slug = base
            suffix = 2
            while row.slug == 'cadeaux-bebe' or Category.objects.filter(slug=row.slug).exists():
                row.slug = f'{base}-{suffix}'
                suffix += 1
            row.position = (Category.objects.aggregate(last=Max('position'))['last'] or 0) + 10
        changed = creating or any(getattr(row, key) != cleaned[key] for key in ('name', 'menu_label', 'show_in_menu'))
        for key in ('name', 'menu_label', 'show_in_menu'):
            setattr(row, key, cleaned[key])
        if changed:
            _persist(row, actor, 'Rubrique créée' if creating else 'Rubrique modifiée', creating=creating)
        return row


def set_category_visibility(pk, revision, show_in_menu, actor):
    _authorized(actor)
    revision = _version(revision)
    if type(show_in_menu) is not bool:
        raise CategoryError('Choisissez d’afficher ou de masquer la rubrique dans le menu.')
    with transaction.atomic():
        _write_lock()
        row = _row(pk, revision)
        if row.show_in_menu != show_in_menu:
            row.show_in_menu = show_in_menu
            _persist(row, actor, 'Rubrique affichée dans le menu' if show_in_menu else 'Rubrique retirée du menu')
        return row


def category_order_snapshot(categories, actor):
    _authorized(actor)
    return signing.dumps({'actor': actor.pk, 'categories': [[row.pk, row.revision] for row in categories]},
                         salt=ORDER_SALT, compress=True)


def _snapshot(token, actor):
    if not isinstance(token, str) or len(token) > 100000:
        raise CategoryError('Rechargez la liste des rubriques avant de continuer.')
    try:
        payload = signing.loads(token, salt=ORDER_SALT, max_age=86400)
    except signing.SignatureExpired as exc:
        raise CategoryConflict('Cette liste a expiré. Rechargez-la avant de modifier son ordre.') from exc
    except signing.BadSignature as exc:
        raise CategoryError('La liste des rubriques n’a pas pu être vérifiée. Rechargez-la.') from exc
    if (not isinstance(payload, dict) or set(payload) != {'actor', 'categories'}
            or payload['actor'] != actor.pk):
        raise CategoryError('Cette liste ne correspond pas à votre session. Rechargez-la.')
    categories = payload['categories']
    if (not isinstance(categories, list) or len(categories) > 2000 or any(
            not isinstance(row, list) or len(row) != 2 or
            any(type(value) is not int or value < 1 for value in row) for row in categories)
            or len({row[0] for row in categories}) != len(categories)):
        raise CategoryError('La liste des rubriques est invalide. Rechargez-la.')
    return categories


def move_category(pk, direction, snapshot, actor):
    _authorized(actor)
    if type(pk) is not int or pk < 1 or direction not in ('up', 'down'):
        raise CategoryError('Choisissez une rubrique à monter ou à descendre.')
    expected = _snapshot(snapshot, actor)
    with transaction.atomic():
        _write_lock()
        rows = list(Category.objects.select_for_update().order_by('position', 'name', 'pk'))
        if [[row.pk, row.revision] for row in rows] != expected:
            raise CategoryConflict(CONFLICT_MESSAGE)
        order = [row.pk for row in rows]
        if pk not in order:
            raise CategoryConflict(CONFLICT_MESSAGE)
        index = order.index(pk)
        destination = index + (-1 if direction == 'up' else 1)
        if not 0 <= destination < len(rows):
            raise CategoryError('Cette rubrique est déjà au début ou à la fin de la liste.')
        rows[index], rows[destination] = rows[destination], rows[index]
        for position, row in enumerate(rows, 1):
            if row.position != position:
                row.position = position
                _persist(row, actor, 'Ordre des rubriques modifié')
        return rows
