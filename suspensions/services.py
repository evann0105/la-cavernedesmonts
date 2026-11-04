"""Clock-based pauses. Reading time never changes stock or payment ledgers."""
from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.utils import timezone
from django.utils.formats import date_format

from connexion.email_verification import email_access_allowed
from core.catalog_access import can_manage_catalog
from .exceptions import PauseConflict, PauseError, PurchaseGateBusy, PurchasesPaused
from .models import PauseConfiguration, PauseEvent, PurchaseAdmission, PurchasePause

DEFAULT_MESSAGE = 'La boutique prend une pause. Le catalogue reste consultable ; les commandes reprendront automatiquement à la date indiquée.'
PARIS = ZoneInfo('Europe/Paris')
LOCK_KEY = (4413005, 1346458963)


def _authorized(actor):
    if actor is None or not can_manage_catalog(actor) or not email_access_allowed(actor):
        raise PermissionDenied


def _lock(*, shared=False, wait=True):
    if not connection.in_atomic_block:
        raise RuntimeError('Le contrôle de la suspension doit être effectué dans une transaction.')
    if connection.vendor == 'postgresql':
        function = ('pg_advisory_xact_lock_shared' if shared else 'pg_advisory_xact_lock') if wait else (
            'pg_try_advisory_xact_lock_shared' if shared else 'pg_try_advisory_xact_lock')
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT {function}(%s, %s)', LOCK_KEY)
            if not wait and not cursor.fetchone()[0]:
                raise PurchaseGateBusy('Les réglages des commandes sont en cours de mise à jour. Réessayez dans quelques instants ; votre panier est conservé.')


def configuration_revision():
    return PauseConfiguration.objects.filter(pk=1).values_list('revision', flat=True).first() or 0


def public_state(at=None):
    at = at or timezone.now()
    active = PurchasePause.objects.filter(stopped_at__isnull=True, starts_at__lte=at, ends_at__gt=at).order_by('starts_at', 'pk').first()
    next_period = PurchasePause.objects.filter(stopped_at__isnull=True, starts_at__gt=at).order_by('starts_at', 'pk').first()
    resumes_at = active.ends_at if active else None
    if active:
        # Touching periods do not briefly reopen the checkout. The visitor sees
        # the end of the whole continuous break, not a misleading intermediate date.
        for following in PurchasePause.objects.filter(stopped_at__isnull=True, starts_at__gte=active.ends_at).order_by('starts_at', 'pk'):
            if following.starts_at > resumes_at:
                break
            resumes_at = max(resumes_at, following.ends_at)
    boundary = active.ends_at if active else next_period.starts_at if next_period else None
    return {'paused': active is not None, 'message': active.message if active else '',
            'starts_at': active.starts_at.isoformat() if active else None,
            'resumes_at': resumes_at.isoformat() if resumes_at else None,
            'next_change_at': boundary.isoformat() if boundary else None,
            'checked_at': at.isoformat(), 'revision': configuration_revision()}


def pause_message(state):
    from django.utils.dateparse import parse_datetime
    resume = parse_datetime(state['resumes_at']) if state.get('resumes_at') else None
    date = date_format(timezone.localtime(resume, PARIS), 'd/m/Y à H:i') if resume else ''
    return ('Les commandes sont temporairement suspendues. '
            + (f'Reprise prévue le {date} (heure de Paris). ' if date else '')
            + 'Votre panier est conservé et le catalogue reste consultable.')


def assert_purchases_open(*, lock=False):
    if lock:
        _lock(shared=True)
    state = public_state()
    if state['paused']:
        raise PurchasesPaused(pause_message(state), state=state)
    return state


def admit_order(order):
    # The caller already holds Order. Pause management only takes the advisory
    # lock and its own rows: it never waits for an Order or inventory lock.
    previous = PurchaseAdmission.objects.filter(order=order).first()
    if previous:
        return previous
    # This caller holds Order. Never wait behind an exclusive pause writer:
    # another reservation may already hold a shared gate while waiting for the
    # same Order. A non-blocking acquisition breaks that three-party cycle.
    _lock(shared=True, wait=False)
    at = timezone.now()
    state = public_state(at=at)
    if state['paused']:
        raise PurchasesPaused(pause_message(state), state=state)
    return PurchaseAdmission.objects.create(order=order, admitted_at=at)


def _locked(expected):
    if type(expected) is not int or expected < 0:
        raise PauseError('Rechargez cette page pour vérifier les pauses programmées.')
    _lock()
    instance = PauseConfiguration.objects.select_for_update().filter(pk=1).first()
    current = instance.revision if instance else 0
    if expected != current:
        raise PauseConflict('Les pauses ont changé dans un autre onglet. Votre saisie est conservée ; comparez les nouvelles dates avant d’enregistrer à nouveau.')
    return instance or PauseConfiguration(revision=0)


def _snapshot(pause):
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in {
        'name': pause.name, 'message': pause.message, 'starts_at': pause.starts_at,
        'ends_at': pause.ends_at, 'stopped_at': pause.stopped_at, 'stop_kind': pause.stop_kind,
        'revision': pause.revision}.items()}


def _persist(config, pause, actor, *, kind, before):
    pause.updated_by = actor
    pause.updated_by_label = actor.get_username()[:150]
    pause.full_clean()
    pause.save()
    config.revision += 1
    config.save()
    PauseEvent.objects.create(pause=pause, kind=kind, actor=actor, actor_label=actor.get_username()[:150],
                              before=before, after=_snapshot(pause))
    return pause


def _text(data, name, limit):
    value = data.get(name)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise PauseError('Renseignez un nom et un message courts pour cette pause.')
    return value.strip()


def _date(data, field):
    value = data.get(field)
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise PauseError('Choisissez des dates et heures valides, selon l’heure de Paris.')
    return value


@transaction.atomic
def save_pause(cleaned_data, actor, pk=None):
    _authorized(actor)
    if not isinstance(cleaned_data, dict):
        raise PauseError('Le formulaire de pause est incomplet.')
    config = _locked(cleaned_data.get('revision'))
    now = timezone.now()
    if pk is not None:
        pause = PurchasePause.objects.select_for_update().filter(pk=pk).first()
        if pause is None:
            raise PauseError('Cette pause n’existe plus.')
        if pause.status_at(now) not in {'active', 'upcoming'}:
            raise PauseError('Cette pause est terminée ou annulée. Programmez une nouvelle période pour suspendre les commandes.')
        before = _snapshot(pause)
        original = (pause.name, pause.message, pause.starts_at, pause.ends_at)
    else:
        pause, before = PurchasePause(), {}
    name, message = _text(cleaned_data, 'name', 120), _text(cleaned_data, 'message', 300)
    mode = cleaned_data.get('mode')
    if mode not in {'now', 'scheduled'}:
        raise PauseError('Choisissez de commencer maintenant ou à une date prévue.')
    if pk and pause.status_at(now) == 'active':
        starts = pause.starts_at
    elif mode == 'now':
        starts = now
    else:
        starts = _date(cleaned_data, 'starts_at')
        if starts <= now:
            raise PauseError('Le début programmé doit être dans le futur. Choisissez « Dès maintenant » pour une pause immédiate.')
    ends = _date(cleaned_data, 'ends_at')
    if ends <= starts or ends <= now:
        raise PauseError('La reprise doit être après le début de la pause et après l’heure actuelle.')
    overlap = PurchasePause.objects.filter(stopped_at__isnull=True, starts_at__lt=ends, ends_at__gt=starts).exclude(pk=pk)
    if overlap.exists():
        raise PauseError('Ces dates chevauchent une autre pause. Modifiez la période existante ou choisissez des dates distinctes.')
    pause.name, pause.message, pause.starts_at, pause.ends_at = name, message, starts, ends
    if pk:
        if (name, message, starts, ends) == original:
            return pause
        pause.revision += 1
    return _persist(config, pause, actor, kind='edit' if pk else 'create', before=before)


@transaction.atomic
def _stop(pk, revision, actor, kind):
    _authorized(actor)
    config = _locked(revision)
    pause = PurchasePause.objects.select_for_update().filter(pk=pk).first()
    if pause is None:
        raise PauseError('Cette pause n’existe plus.')
    now = timezone.now()
    state = pause.status_at(now)
    if (kind == 'cancel' and state != 'upcoming') or (kind == 'resume' and state != 'active'):
        raise PauseConflict('Cette pause a déjà changé d’état. Rechargez sa fiche pour voir les dates actuelles.')
    before = _snapshot(pause)
    pause.stopped_at, pause.stop_kind = now, kind
    pause.revision += 1
    return _persist(config, pause, actor, kind=kind, before=before)


def cancel_pause(pk, revision, actor):
    return _stop(pk, revision, actor, 'cancel')


def resume_pause(pk, revision, actor):
    return _stop(pk, revision, actor, 'resume')
