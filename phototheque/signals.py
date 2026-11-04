"""Capture only committed uploads. A library failure cannot undo a valid shop save."""
from contextvars import ContextVar
import logging

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_save, pre_save

from .inventory import MODEL_FIELDS, register_reference, source_metadata


logger = logging.getLogger(__name__)
_actor = ContextVar('phototheque_upload_actor', default=None)


class PhotoActorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        actor = getattr(request, 'user', None)
        token = _actor.set(actor if actor is not None and actor.is_authenticated else None)
        try:
            return self.get_response(request)
        finally:
            _actor.reset(token)


def _before_save(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw:
        return
    fields = MODEL_FIELDS[sender._meta.label]
    if update_fields is not None:
        fields = tuple(field for field in fields if field in update_fields)
    instance._phototheque_fields = fields
    instance._phototheque_previous = (sender.objects.filter(pk=instance.pk).values(*fields).first()
                                     if instance.pk and fields else None) or {}


def _register_committed(label, pk, field, name, metadata, actor_id, using):
    try:
        model = apps.get_model(label)
        current = model.objects.using(using).filter(pk=pk).values_list(field, flat=True).first()
        if current != name:
            return
        actor = get_user_model().objects.using(using).filter(pk=actor_id).first() if actor_id else None
        register_reference('media', name, actor=actor, **metadata)
    except Exception:
        # sync_phototheque re-reads the committed business references later.
        # Log source identity, never the contents of an uploaded file.
        logger.exception('Bibliothèque : capture impossible pour %s %s (%s).', label, pk, field)


def _after_save(sender, instance, raw=False, using='default', **kwargs):
    if raw:
        return
    previous = getattr(instance, '_phototheque_previous', {})
    fields = getattr(instance, '_phototheque_fields', ())
    actor = _actor.get() or getattr(instance, 'updated_by', None)
    actor_id = getattr(actor, 'pk', None)
    for field in fields:
        image = getattr(instance, field)
        if not image or not image.name or previous.get(field) == image.name:
            continue
        try:
            metadata = source_metadata(instance, field)
        except Exception:
            logger.exception('Bibliothèque : libellé impossible pour %s %s (%s).', sender._meta.label, instance.pk, field)
            continue
        # Capture scalars now; the mutable instance may be edited again before
        # an enclosing transaction commits. Re-read the persisted path later.
        callback = lambda label=sender._meta.label, pk=instance.pk, field=field, name=image.name, metadata=metadata: _register_committed(
            label, pk, field, name, metadata, actor_id, using)
        transaction.on_commit(callback, using=using, robust=True)


def connect():
    for label in MODEL_FIELDS:
        pre_save.connect(_before_save, sender=label, weak=False, dispatch_uid=f'phototheque.before.{label}')
        post_save.connect(_after_save, sender=label, weak=False, dispatch_uid=f'phototheque.after.{label}')
