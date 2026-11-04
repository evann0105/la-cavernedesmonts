"""Signed, repeat-safe restoration of immutable catalogue and home versions."""
import hashlib
import json
from importlib import import_module
from uuid import uuid4

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.urls import reverse

from connexion.email_verification import email_access_allowed
from core.catalog_access import can_manage_catalog
from .exceptions import HistoryConflict, HistoryError
from .media import archive_files, asset_warnings
from .models import HistoryVersion, RestorationReceipt

SALT = 'historique-restauration-v1'
MAX_AGE = 30 * 60


def authorized(actor):
    if actor is None or not can_manage_catalog(actor) or not email_access_allowed(actor):
        raise PermissionDenied


def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(snapshot, cls=DjangoJSONEncoder, sort_keys=True,
        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def record(scope, object_id, snapshot, *, actor=None, object_label='', label='',
           source='edit', restored_from=None, assets=()):
    """Caller holds the object lock and its surrounding business transaction."""
    if scope not in {'product', 'homepage'} or type(object_id) is not int or object_id < 1:
        raise HistoryError('Cet objet ne peut pas être conservé dans l’historique.')
    snapshot = json.loads(json.dumps(snapshot, cls=DjangoJSONEncoder, ensure_ascii=False))
    digest = fingerprint(snapshot)
    previous = HistoryVersion.objects.filter(scope=scope, object_id=object_id).order_by('-sequence').first()
    if previous and previous.fingerprint == digest:
        return previous
    return HistoryVersion.objects.create(scope=scope, object_id=object_id,
        object_label=str(object_label)[:200], sequence=previous.sequence + 1 if previous else 1,
        snapshot=snapshot, fingerprint=digest, assets=archive_files(assets),
        actor=actor, actor_label=actor.get_username()[:150] if actor else 'État conservé automatiquement',
        label=str(label or ('État initial conservé' if source == 'baseline' else 'Modification enregistrée'))[:250],
        source=source, restored_from=restored_from)


def _backend(version):
    if version.scope not in {'product', 'homepage'}:
        raise HistoryError('Ce type de version n’est plus pris en charge.')
    return import_module('historique.products' if version.scope == 'product' else 'historique.homepage')


def snapshot_current(version):
    backend = _backend(version)
    if version.scope == 'product':
        from core.models import Product
        product = Product.objects.filter(pk=version.object_id).first()
        if product is None:
            raise HistoryError('Cette fiche a été supprimée. Sa version reste consultable, mais ne peut pas recréer un article lié à des commandes.')
        return backend.snapshot_product(product)
    return backend.snapshot_homepage()


def object_url(version):
    return (reverse('core:catalog_edit', args=[version.object_id]) if version.scope == 'product'
            else reverse('core:catalog_homepage'))


def prepare_restore(actor, version):
    authorized(actor)
    current = snapshot_current(version)
    if fingerprint(current) == version.fingerprint:
        raise HistoryError('Cette version correspond déjà au contenu actuel.')
    problems = asset_warnings(version)
    if problems:
        raise HistoryError(' '.join(problems))
    backend = _backend(version)
    if hasattr(backend, 'validate_restore'):
        backend.validate_restore(version)
    warnings = backend.restore_warnings(version) if hasattr(backend, 'restore_warnings') else []
    token = signing.dumps({'schema': 1, 'version': version.pk, 'actor': actor.pk,
        'fingerprint': fingerprint(current), 'target': version.fingerprint, 'nonce': uuid4().hex},
        salt=SALT, compress=True)
    return {'token': token, 'current_snapshot': current, 'warnings': warnings,
            'target_label': version.object_label, 'object_url': object_url(version)}


def _read_token(actor, token, expected_entry_id):
    if not isinstance(token, str) or len(token) > 4000:
        raise HistoryError('Cette confirmation est invalide. Vérifiez à nouveau la restauration.')
    try:
        payload = signing.loads(token, salt=SALT, max_age=MAX_AGE)
    except (signing.BadSignature, TypeError, ValueError) as error:
        raise HistoryError('La confirmation a expiré. Vérifiez à nouveau les différences avant de restaurer.') from error
    if (not isinstance(payload, dict) or set(payload) != {'schema', 'version', 'actor', 'fingerprint', 'target', 'nonce'}
            or payload['schema'] != 1 or payload['actor'] != actor.pk
            or type(payload['version']) is not int or payload['version'] < 1
            or (expected_entry_id is not None and payload['version'] != expected_entry_id)
            or any(not isinstance(payload[key], str) or len(payload[key]) != size
                   for key, size in [('fingerprint', 64), ('target', 64), ('nonce', 32)])):
        raise HistoryError('Cette confirmation ne correspond pas à cette version ou à votre compte.')
    return payload


@transaction.atomic
def apply_restore(actor, token, expected_entry_id=None):
    authorized(actor)
    payload = _read_token(actor, token, expected_entry_id)
    try:
        version = HistoryVersion.objects.select_for_update().get(pk=payload['version'])
    except HistoryVersion.DoesNotExist as error:
        raise HistoryError('Cette version n’est plus disponible.') from error
    if version.fingerprint != payload['target'] or fingerprint(version.snapshot) != version.fingerprint:
        raise HistoryError('Cette version est endommagée. Aucune restauration n’a été effectuée.')
    receipt = RestorationReceipt.objects.filter(nonce=payload['nonce']).select_related('result').first()
    if receipt:
        if receipt.actor_id != actor.pk or receipt.version_id != version.pk:
            raise HistoryError('Cette confirmation appartient à une autre opération.')
        return receipt.result
    backend = _backend(version)
    restore = backend.restore_product if version.scope == 'product' else backend.restore_homepage
    result = restore(version, actor=actor, expected_fingerprint=payload['fingerprint'])
    RestorationReceipt.objects.create(nonce=payload['nonce'], version=version, actor=actor, result=result)
    return result
