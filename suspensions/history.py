"""Private presentation of frozen pause events; never infer missing old values."""
from datetime import datetime, timezone as datetime_timezone
from zoneinfo import ZoneInfo

from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_datetime


PARIS = ZoneInfo('Europe/Paris')
MISSING = object()
EVENT_LABELS = {
    'create': 'Création de la pause',
    'edit': 'Modification de la pause',
    'cancel': 'Annulation de la pause programmée',
    'resume': 'Fin anticipée de la pause',
}
FIELDS = (
    ('name', 'Nom interne', 'text'),
    ('message', 'Message affiché aux clients', 'text'),
    ('starts_at', 'Début de la pause', 'date'),
    ('ends_at', 'Reprise automatique prévue', 'date'),
    ('stop_kind', 'Décision', 'decision'),
    ('stopped_at', 'Date de fin anticipée', 'date'),
)
DECISIONS = {'': 'Pause non arrêtée',
             'cancel': 'Pause programmée annulée', 'resume': 'Pause terminée plus tôt'}


def _datetime(value):
    if isinstance(value, str):
        try:
            value = parse_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    if not isinstance(value, datetime) or timezone.is_naive(value):
        return None
    try:
        return value.astimezone(PARIS)
    except (ValueError, OverflowError):
        return None


def _date_value(value, *, precise=False):
    """An ISO instant is converted to Paris, independently of the active locale."""
    moment = _datetime(value)
    if moment is None:
        return {'text': 'Date non conservée ou illisible', 'iso': '', 'title': '', 'known': False}
    seconds = precise or moment.second != 0 or moment.microsecond != 0
    text = moment.strftime('%d/%m/%Y à %H:%M:%S' if seconds else '%d/%m/%Y à %H:%M')
    offset = moment.strftime('%z')
    offset = f'UTC{offset[:3]}:{offset[3:]}'
    # During the autumn clock change, two distinct instants share the same
    # Paris clock time. Preserve that distinction visibly, not only in a title.
    if moment.replace(fold=1 - moment.fold).utcoffset() != moment.utcoffset():
        text += f' ({offset})'
    return {'text': text, 'iso': moment.isoformat(), 'title': f'Heure de Paris · {offset}', 'known': True}


def _value(value, kind, *, key='', creation_before=False):
    if creation_before:
        return {'text': '—', 'iso': '', 'title': '', 'known': True}
    if value is MISSING:
        return {'text': 'Valeur non conservée', 'iso': '', 'title': '', 'known': False}
    if value is None or value == '':
        text = 'Aucune' if key == 'stopped_at' else DECISIONS[''] if key == 'stop_kind' else 'Non renseigné'
        return {'text': text, 'iso': '', 'title': '', 'known': True}
    if kind == 'date':
        return _date_value(value, precise=key == 'stopped_at')
    if not isinstance(value, str):
        return {'text': 'Valeur ancienne illisible', 'iso': '', 'title': '', 'known': False}
    if kind == 'decision':
        return {'text': DECISIONS.get(value, 'Décision ancienne non reconnue'),
                'iso': '', 'title': '', 'known': value in DECISIONS}
    return {'text': value, 'iso': '', 'title': '', 'known': True}


def _equal(before, after, kind):
    if before is MISSING or after is MISSING:
        return before is after
    if kind == 'date':
        first, second = _datetime(before), _datetime(after)
        if first is not None and second is not None:
            # Comparing two datetimes carrying the same ZoneInfo directly
            # ignores fold. UTC comparison correctly distinguishes DST twins.
            return first.astimezone(datetime_timezone.utc) == second.astimezone(datetime_timezone.utc)
    return before == after


def event_details(event):
    """Return plain escaped-by-template values from one PauseEvent snapshot.

    Only actual changes to the six supported business fields are displayed.
    Revisions and unknown metadata never become customer-facing history rows.
    """
    before_valid, after_valid = isinstance(event.before, dict), isinstance(event.after, dict)
    before = event.before if before_valid else {}
    after = event.after if after_valid else {}
    creating = event.kind == 'create'
    rows = []
    required = ('name', 'message', 'starts_at', 'ends_at')
    incomplete = (not after_valid or any(key not in after for key in required)
                  or (not creating and (not before_valid or any(key not in before for key in required))))
    for key, label, kind in FIELDS:
        previous, current = before.get(key, MISSING), after.get(key, MISSING)
        if creating:
            if key in ('stop_kind', 'stopped_at') and current in (MISSING, None, ''):
                continue
        elif _equal(previous, current, kind):
            continue
        if key == 'stopped_at':
            action = after.get('stop_kind')
            label = ('Date de l’annulation' if action == 'cancel' else
                     'Date de reprise anticipée' if action == 'resume' else label)
        old = _value(previous, kind, key=key, creation_before=creating)
        new = _value(current, kind, key=key)
        incomplete = incomplete or not old['known'] or not new['known']
        rows.append({'key': key, 'label': label, 'before': old, 'after': new})
    created = _date_value(event.created_at, precise=True)
    actor_label = event.actor_label if isinstance(event.actor_label, str) and event.actor_label.strip() else 'Auteur non conservé'
    return {
        'id': event.pk, 'kind': event.kind,
        'label': EVENT_LABELS.get(event.kind, 'Événement conservé'),
        'created': created, 'actor_label': actor_label, 'rows': rows,
        'incomplete': incomplete,
        'empty_message': 'Aucune modification détaillée des réglages n’est conservée pour cet événement.',
    }


def _ordered_events(events):
    if isinstance(events, QuerySet) and not events.query.is_sliced:
        return events.order_by('-created_at', '-pk')
    def key(event):
        moment = _datetime(event.created_at)
        return (moment.astimezone(datetime_timezone.utc) if moment else datetime.min.replace(tzinfo=datetime_timezone.utc),
                event.pk if isinstance(event.pk, int) else 0)
    return sorted(events, key=key, reverse=True)


def event_rows(events):
    """Present every supplied event, newest first with a stable primary-key tie."""
    return [event_details(event) for event in _ordered_events(events)]


def history_context(events, page_number=None, per_page=12):
    """Paginate the full relation, never a pre-sliced last-twelve queryset."""
    if type(per_page) is not int or not 1 <= per_page <= 50:
        raise ValueError('La taille d’une page d’historique doit être comprise entre 1 et 50.')
    page = Paginator(_ordered_events(events), per_page).get_page(page_number)
    return {'pause_history': event_rows(page.object_list), 'pause_history_page': page}
