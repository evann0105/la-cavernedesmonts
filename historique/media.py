"""Content-addressed local copies. No URL fetching and no historical deletion."""
import hashlib
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.templatetags.static import static

from .exceptions import HistoryError

MAX_BYTES = 64 * 1024 * 1024
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif'}


def safe_name(name):
    if not isinstance(name, str) or not name or len(name) > 500:
        raise HistoryError('Une ancienne photo ne possède plus de référence valide.')
    path = PurePosixPath(name)
    if (path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name
            or '?' in name or '#' in name or '\x00' in name or path.suffix.lower() not in EXTENSIONS):
        raise HistoryError('Une ancienne photo ne possède plus de référence valide.')
    return str(path)


def _read(kind, name):
    name = safe_name(name)
    if kind == 'media':
        # Reject symlinks escaping a filesystem storage. Other configured
        # storages retain their own access controls; no arbitrary URL is used.
        try:
            location = Path(default_storage.location).resolve()
            if not Path(default_storage.path(name)).resolve().is_relative_to(location):
                raise HistoryError('Une photo sort du dossier autorisé.')
        except (AttributeError, NotImplementedError):
            pass
        stream = default_storage.open(name, 'rb')
    elif kind == 'static':
        found = finders.find(name)
        if not found:
            raise FileNotFoundError(name)
        path = Path(found)
        # The finder selects a trusted static directory. Never follow an
        # in-directory symlink to an unrelated file.
        parent = path
        for _ in PurePosixPath(name).parts:
            if parent.is_symlink():
                raise HistoryError('Une photo sort du dossier autorisé.')
            parent = parent.parent
        stream = path.open('rb')
    else:
        raise HistoryError('Le type de cette ancienne photo est inconnu.')
    with stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HistoryError('Une photo dépasse la taille maximale de conservation (64 Mo).')
    return data


def archive_files(assets):
    result, seen = [], set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise HistoryError('La liste des photos à conserver est invalide.')
        kind, name = asset.get('kind'), asset.get('name')
        if not name:
            continue
        safe_name(name)
        if kind not in {'static', 'media'}:
            raise HistoryError('La liste des photos à conserver est invalide.')
        if (kind, name) in seen:
            continue
        seen.add((kind, name))
        try:
            data = _read(kind, name)
        except (FileNotFoundError, OSError):
            # A previously broken legacy image must not make its whole product
            # uneditable. The history UI explicitly blocks its recovery.
            result.append({'kind': kind, 'name': name, 'missing': True})
            continue
        digest = hashlib.sha256(data).hexdigest()
        archive = f'historique-assets/{digest[:2]}/{digest}{PurePosixPath(name).suffix.lower()}'
        if default_storage.exists(archive):
            if hashlib.sha256(_read('media', archive)).hexdigest() != digest:
                raise HistoryError('Une copie de photo conservée est endommagée. La modification n’a pas été enregistrée.')
        else:
            saved = default_storage.save(archive, ContentFile(data))
            # Concurrent identical saves can choose an alternative name. Keep
            # precisely the verified name returned by the storage.
            archive = safe_name(saved)
        result.append({'kind': kind, 'name': name, 'archive': archive,
                       'sha256': digest, 'size': len(data)})
    return result


def _matches(kind, name, digest):
    try:
        data = _read(kind, name)
    except (OSError, HistoryError):
        return False
    return digest is None or hashlib.sha256(data).hexdigest() == digest


def asset_name(version, kind, name, *, prefer_archive=False):
    name = safe_name(name)
    asset = next((item for item in version.assets
                  if item.get('kind') == kind and item.get('name') == name), None)
    if asset is None:
        raise HistoryError('Cette photo n’a pas été conservée avec cette version. Choisissez une autre version ou remettez la photo dans la fiche.')
    digest = asset.get('sha256')
    if not prefer_archive and _matches(kind, name, digest):
        return kind, name
    archive = asset.get('archive')
    if archive and digest and _matches('media', archive, digest):
        return 'media', safe_name(archive)
    # Legacy missing assets can become available again after a backup recovery.
    if not prefer_archive and _matches(kind, name, digest):
        return kind, name
    raise HistoryError('Une photo de cette version est introuvable ou endommagée. Aucune restauration n’a été effectuée.')


def media_name(version, name):
    return asset_name(version, 'media', name)[1] if name else ''


def static_name(version, name, *, prefer_archive=False):
    return asset_name(version, 'static', name, prefer_archive=prefer_archive)


def image_url(name, kind='media', *, version=None):
    if not name:
        return ''
    try:
        if version is not None:
            kind, name = asset_name(version, kind, name)
        else:
            name = safe_name(name)
        return static(name) if kind == 'static' else default_storage.url(name)
    except (HistoryError, ValueError):
        return ''


def asset_warnings(version):
    warnings = []
    for asset in version.assets:
        try:
            asset_name(version, asset.get('kind'), asset.get('name'))
        except HistoryError as error:
            if str(error) not in warnings:
                warnings.append(str(error))
    return warnings
