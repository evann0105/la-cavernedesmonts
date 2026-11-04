"""Validated, immutable local image copies; all merchant writes require permission."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
import unicodedata
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from connexion.email_verification import email_access_allowed
from core.catalog_access import can_manage_catalog
from .models import Folder, Photo, folder_key


MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 64_000_000
MAX_UPLOADS = 12
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
_LIBRARY_TOKEN = object()


class PhotoError(ValueError):
    pass


def _authorized(actor):
    if actor is None or not can_manage_catalog(actor) or not email_access_allowed(actor):
        raise PermissionDenied


def _id(value):
    try:
        if isinstance(value, bool):
            raise ValueError
        value = int(value)
        if not 0 < value <= 9223372036854775807:
            raise ValueError
        return value
    except (ValueError, TypeError, OverflowError):
        raise PhotoError('Cette photo ou ce dossier n’est pas disponible.') from None


def _text(value, maximum, *, fallback=''):
    if not isinstance(value, str):
        raise PhotoError('Renseignez un nom valide.')
    value = unicodedata.normalize('NFKC', value.strip())
    if not value:
        value = fallback
    if not value or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise PhotoError(f'Choisissez un nom de 1 à {maximum} caractères.')
    return value


def _folder(folder_id):
    if folder_id in (None, ''):
        return None
    found = Folder.objects.filter(pk=_id(folder_id)).first()
    if found is None:
        raise PhotoError('Ce dossier n’existe plus. Choisissez un autre dossier.')
    return found


@transaction.atomic
def create_folder(name, *, actor):
    _authorized(actor)
    name = _text(name, 80)
    try:
        with transaction.atomic():
            return Folder.objects.create(name=name)
    except IntegrityError:
        raise PhotoError('Un dossier porte déjà ce nom, même avec une casse différente.') from None


@transaction.atomic
def rename_folder(folder_id, name, *, actor):
    _authorized(actor)
    name = _text(name, 80)
    folder = Folder.objects.select_for_update().filter(pk=_id(folder_id)).first()
    if folder is None:
        raise PhotoError('Ce dossier n’existe plus.')
    folder.name = name
    try:
        with transaction.atomic():
            folder.save(update_fields=['name', 'name_key'])
    except IntegrityError:
        raise PhotoError('Un dossier porte déjà ce nom, même avec une casse différente.') from None
    return folder


def _system_folder(name):
    system_key = {'Produits': 'products', 'Accueil': 'homepage', 'Pages': 'pages'}.get(name)
    if system_key:
        existing = Folder.objects.filter(system_key=system_key).first()
        if existing:
            return existing
    try:
        with transaction.atomic():
            folder, _ = Folder.objects.get_or_create(name_key=folder_key(name), defaults={'name': name, 'system_key': system_key})
            if system_key and not folder.system_key:
                folder.system_key = system_key
                folder.save(update_fields=['system_key'])
            return folder
    except IntegrityError:
        if system_key:
            existing = Folder.objects.filter(system_key=system_key).first()
            if existing:
                return existing
        return Folder.objects.get(name_key=folder_key(name))


def safe_name(name):
    if not isinstance(name, str) or not name or len(name) > 500:
        raise PhotoError('La référence de cette photo est invalide.')
    path = PurePosixPath(name)
    if (path.is_absolute() or '..' in path.parts or any(char in name for char in ('\\', ':', '?', '#', '\x00'))
            or path.suffix.lower() not in EXTENSIONS):
        raise PhotoError('La référence de cette photo est invalide.')
    return str(path)


def _no_symlinks(path, base):
    path, base = Path(path), Path(base)
    try:
        path.relative_to(base)
    except ValueError:
        raise PhotoError('Cette photo ne se trouve pas dans le dossier autorisé.') from None
    current = path
    while current != base:
        if current.is_symlink():
            raise PhotoError('Les liens vers d’autres fichiers ne sont pas acceptés.')
        current = current.parent
    if base.is_symlink() or not path.resolve().is_relative_to(base.resolve()):
        raise PhotoError('Les liens vers d’autres fichiers ne sont pas acceptés.')


def read_source(kind, name, *, library=False):
    """Read only referenced shop pictures, never arbitrary media or remote URLs."""
    name = safe_name(name)
    if kind == 'media':
        allowed = ('phototheque/',) if library else ('products/', 'homepage/', 'editorial/')
        if not name.startswith(allowed):
            raise PhotoError('Ce fichier ne fait pas partie des photos de la boutique.')
        try:
            _no_symlinks(default_storage.path(name), default_storage.location)
        except (AttributeError, NotImplementedError):
            raise PhotoError('Le stockage local des photos n’est pas disponible.') from None
        opener = lambda: default_storage.open(name, 'rb')
    elif kind == 'static' and not library:
        if not name.startswith('core/img/'):
            raise PhotoError('Ce fichier ne fait pas partie des photos de la boutique.')
        found = finders.find(name)
        if not found:
            raise PhotoError('Cette photo est introuvable.')
        path = Path(found)
        base = path
        for _ in PurePosixPath(name).parts:
            base = base.parent
        _no_symlinks(path, base)
        opener = lambda: path.open('rb')
    else:
        raise PhotoError('La référence de cette photo est invalide.')
    try:
        with opener() as stream:
            data = stream.read(MAX_BYTES + 1)
    except OSError:
        raise PhotoError('Cette photo est introuvable ou ne peut pas être lue.') from None
    if len(data) > MAX_BYTES:
        raise PhotoError('Chaque photo doit peser moins de 10 Mo.')
    return data


def _read_upload(upload):
    if not hasattr(upload, 'read'):
        raise PhotoError('Choisissez un fichier JPEG, PNG ou WebP.')
    try:
        upload.seek(0)
        data = upload.read(MAX_BYTES + 1)
    except (AttributeError, OSError, ValueError):
        raise PhotoError('Cette photo ne peut pas être lue.') from None
    finally:
        try:
            upload.seek(0)
        except (AttributeError, OSError, ValueError):
            pass
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise PhotoError('Chaque photo doit peser moins de 10 Mo.')
    return data


def _normalize(data):
    if not data or len(data) > MAX_BYTES:
        raise PhotoError('Choisissez une photo de moins de 10 Mo.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as opened:
                if opened.format not in ('JPEG', 'MPO', 'PNG', 'WEBP'):
                    raise PhotoError('Utilisez une photo JPEG, PNG ou WebP.')
                if opened.width * opened.height > MAX_PIXELS:
                    raise PhotoError('Cette photo dépasse 64 mégapixels.')
                preserve = (opened.format == 'WEBP' and max(opened.size) <= 2400
                            and getattr(opened, 'n_frames', 1) == 1
                            and not any(opened.info.get(key) for key in ('exif', 'icc_profile', 'xmp')))
                opened.seek(0)
                opened.draft('RGB', (2400, 2400))
                opened.load()
                image = ImageOps.exif_transpose(opened)
                image.thumbnail((2400, 2400))
                image = image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB')
                if preserve:
                    optimized = data
                else:
                    output = BytesIO()
                    image.save(output, format='WEBP', quality=88)
                    optimized = output.getvalue()
                width, height = image.size
                image.thumbnail((360, 360))
                preview = BytesIO()
                image.save(preview, format='WEBP', quality=82)
        return optimized, preview.getvalue(), width, height
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        if isinstance(error, PhotoError):
            raise
        raise PhotoError('Cette photo est illisible. Choisissez un fichier JPEG, PNG ou WebP valide.') from None


def _digest_lock(digest):
    if connection.vendor == 'postgresql':
        key = int(digest[:8], 16)
        if key >= 2 ** 31:
            key -= 2 ** 32
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s, %s)', [4413006, key])


def _original_name(value):
    value = str(value or '').replace('\\', '/').rsplit('/', 1)[-1]
    return ''.join(char for char in value if ord(char) >= 32)[:255] or 'photo.webp'


def _file(name, data, written):
    try:
        _no_symlinks(default_storage.path(name), default_storage.location)
    except (AttributeError, NotImplementedError):
        raise PhotoError('Le stockage local des photos n’est pas disponible.') from None
    if default_storage.exists(name):
        current = read_source('media', name, library=True)
        if sha256(current).digest() != sha256(data).digest():
            raise PhotoError('Une copie conservée de cette photo est endommagée. Contactez le responsable du site.')
        return name
    # Storage chooses a fresh filename in the unlikely event of a concurrent
    # filesystem writer. Never overwrite a file already used by another page.
    saved = default_storage.save(name, ContentFile(data))
    written.append(saved)
    return saved


def _store(data, *, original_name, title='', actor=None, folder=None, folder_name='', source_label='', prepared=None):
    source_digest = sha256(data).hexdigest()
    previous = Photo.objects.filter(Q(digest=source_digest) | Q(source_digest=source_digest)).first()
    if previous:
        _photo_bytes(previous)
        return previous, False
    optimized, thumbnail, width, height = prepared if prepared is not None else _normalize(data)
    digest = sha256(optimized).hexdigest()
    written = []
    try:
        with transaction.atomic():
            _digest_lock(digest)
            previous = Photo.objects.filter(digest=digest).first()
            if previous:
                _photo_bytes(previous)
                return previous, False
            if folder is None and folder_name:
                folder = _system_folder(folder_name)
            original_name = _original_name(original_name)
            title = _text(title, 160, fallback=Path(original_name).stem.strip()[:160] or 'Photo')
            image_name = _file(f'phototheque/images/{digest[:2]}/{digest}.webp', optimized, written)
            thumb_name = _file(f'phototheque/thumbnails/{digest[:2]}/{digest}.webp', thumbnail, written)
            photo = Photo.objects.create(image=image_name, thumbnail=thumb_name, digest=digest,
                source_digest=source_digest, title=title, original_name=original_name,
                width=width, height=height, size=len(optimized), folder=folder,
                uploaded_by=actor, source_label=source_label[:160])
            return photo, True
    except Exception:
        # Immutable orphan files remain invisible without their database row.
        # Never delete after rollback: a concurrent committed capture may have
        # adopted the same content-addressed files after the digest lock ended.
        raise


def upload_photos(uploads, *, actor, folder_id=None):
    _authorized(actor)
    uploads = list(uploads)
    if not 1 <= len(uploads) <= MAX_UPLOADS:
        raise PhotoError('Ajoutez entre 1 et 12 photos à la fois.')
    folder = _folder(folder_id)
    uploads_data = [(_read_upload(upload), _original_name(getattr(upload, 'name', 'photo'))) for upload in uploads]
    # Validate the whole selection before writing anything. One bad file must
    # not silently leave a partially uploaded group in the seller's library.
    prepared = [(index, data, name, _normalize(data)) for index, (data, name) in enumerate(uploads_data)]
    with transaction.atomic():
        by_index = {}
        # A stable digest order avoids opposite-order batch uploads
        # deadlocking while both transactions hold their first image lock.
        for index, data, name, normalized in sorted(prepared, key=lambda row: sha256(row[3][0]).hexdigest()):
            by_index[index] = _store(data, original_name=name, actor=actor, folder=folder,
                                    source_label='Ajout à la bibliothèque', prepared=normalized)
    return [by_index[index] for index in range(len(uploads))]


@transaction.atomic
def update_photo(photo_id, *, actor, title, folder_id=None):
    _authorized(actor)
    title, folder = _text(title, 160), _folder(folder_id)
    photo = Photo.objects.select_for_update().filter(pk=_id(photo_id)).first()
    if photo is None:
        raise PhotoError('Cette photo n’existe plus.')
    photo.title, photo.folder = title, folder
    photo.save(update_fields=['title', 'folder'])
    return photo


@transaction.atomic
def _archive(photo_id, actor, archived):
    _authorized(actor)
    photo = Photo.objects.select_for_update().filter(pk=_id(photo_id)).first()
    if photo is None:
        raise PhotoError('Cette photo n’existe plus.')
    if archived and photo.archived_at is None:
        photo.archived_at = timezone.now()
    elif not archived:
        photo.archived_at = None
    photo.save(update_fields=['archived_at'])
    return photo


def archive_photo(photo_id, *, actor):
    return _archive(photo_id, actor, True)


def restore_photo(photo_id, *, actor):
    return _archive(photo_id, actor, False)


class PhotoContentFile(ContentFile):
    def __init__(self, data, *, digest, token):
        super().__init__(data, name=f'{digest}.webp')
        self._phototheque_token = token
        self._phototheque_digest = digest


def is_library_photo_file(value):
    if not isinstance(value, PhotoContentFile) or getattr(value, '_phototheque_token', None) is not _LIBRARY_TOKEN:
        return False
    try:
        data = _read_upload(value)
    except PhotoError:
        return False
    return sha256(data).hexdigest() == value._phototheque_digest


def _photo_bytes(photo):
    data = read_source('media', photo.image.name, library=True)
    if sha256(data).hexdigest() != photo.digest or len(data) != photo.size:
        raise PhotoError('Le fichier de cette photo est endommagé. Choisissez une autre photo.')
    return data


def resolve_photo(photo_id):
    photo = Photo.objects.filter(pk=_id(photo_id)).first()
    if photo is None:
        raise PhotoError('Cette photo n’est plus disponible dans la bibliothèque.')
    if photo.archived_at:
        raise PhotoError('Cette photo est archivée. Réactivez-la avant de la réutiliser.')
    data = _photo_bytes(photo)
    normalized, _, width, height = _normalize(data)
    if sha256(normalized).hexdigest() != photo.digest or (width, height) != (photo.width, photo.height):
        raise PhotoError('Le fichier de cette photo est endommagé. Choisissez une autre photo.')
    return PhotoContentFile(data, digest=photo.digest, token=_LIBRARY_TOKEN)


def photo_urls(photo):
    """Cheap, read-only grid URLs. Byte integrity is checked before reuse."""
    result = {'url': '', 'thumb': ''}
    for key, field, prefix in (('url', photo.image, 'phototheque/images/'),
                               ('thumb', photo.thumbnail, 'phototheque/thumbnails/')):
        try:
            name = safe_name(field.name)
            if not name.startswith(prefix):
                continue
            path = Path(default_storage.path(name))
            _no_symlinks(path, default_storage.location)
            if path.is_file():
                result[key] = default_storage.url(name)
        except (PhotoError, OSError, ValueError, AttributeError, NotImplementedError):
            pass
    return result
