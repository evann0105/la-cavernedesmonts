"""File widgets accepting bounded library identifiers, never file paths or URLs."""
from dataclasses import dataclass
import json

from django import forms
from django.forms.widgets import FILE_INPUT_CONTRADICTION


@dataclass(frozen=True)
class LibrarySelection:
    ids: tuple
    uploads: tuple = ()
    error: str = ''


def parse_ids(raw):
    if raw in (None, ''):
        return ()
    if not isinstance(raw, str) or len(raw) > 512:
        raise ValueError
    values = json.loads(raw)
    if not isinstance(values, list) or len(values) > 12:
        raise ValueError
    result = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError
        value = str(value)
        if not value.isascii() or not value.isdecimal() or len(value) > 19 or not 0 < int(value) <= 9223372036854775807:
            raise ValueError
        value = str(int(value))
        if value not in result:
            result.append(value)
    return tuple(result)


def selection_files(data, *, multiple=False, maximum=12):
    """Resolve only trusted DB references; resolution itself has no side effects."""
    if not isinstance(data, LibrarySelection):
        return data
    if data.error:
        raise forms.ValidationError(data.error, code='invalid_library_selection')
    if not multiple and len(data.ids) + len(data.uploads) > 1:
        raise forms.ValidationError('Choisissez une seule photo : un fichier ou une photo de la bibliothèque.', code='conflicting_photo_sources')
    if len(data.ids) + len(data.uploads) > maximum:
        raise forms.ValidationError('Sélectionnez au maximum %(maximum)s photos, fichiers et bibliothèque compris.', params={'maximum': maximum}, code='too_many_photos')
    from .services import PhotoError, resolve_photo
    try:
        photos = [*data.uploads, *(resolve_photo(pk) for pk in data.ids)]
    except PhotoError as error:
        raise forms.ValidationError(str(error), code='unavailable_library_photo') from error
    return photos if multiple else (photos[0] if photos else None)


class LibraryInputMixin:
    template_name = 'phototheque/widgets/input.html'
    native_template = 'django/forms/widgets/file.html'
    library_limit = 1

    def value_from_datadict(self, data, files, name):
        native = super().value_from_datadict(data, files, name)
        raw = data.get(name + '-library', '')
        try:
            ids = parse_ids(raw)
            error = ''
        except (ValueError, TypeError, json.JSONDecodeError):
            ids, error = (), 'La sélection de la bibliothèque est invalide. Choisissez à nouveau les photos.'
        self.library_ids = ids
        if error:
            return LibrarySelection((), error=error)
        if not ids:
            return native
        if native is FILE_INPUT_CONTRADICTION or native is False:
            return FILE_INPUT_CONTRADICTION
        uploads = tuple(native) if isinstance(native, (list, tuple)) else ((native,) if native else ())
        return LibrarySelection(ids, uploads)

    def value_omitted_from_data(self, data, files, name):
        return (not data.get(name + '-library') and super().value_omitted_from_data(data, files, name))

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        ids = getattr(self, 'library_ids', ())
        selected = []
        if ids:
            from .models import Photo
            from .services import photo_urls
            photos = {str(photo.pk): photo for photo in Photo.objects.filter(pk__in=ids)}
            for pk in ids:
                photo = photos.get(pk)
                urls = photo_urls(photo) if photo and photo.archived_at is None else {'url': '', 'thumb': ''}
                selected.append({'id': pk, 'title': photo.title if photo else 'Photo indisponible', **urls})
        context['widget'].update(native_template=self.native_template,
            library_value=json.dumps(ids), library_selected=selected,
            library_limit=self.library_limit, library_multiple=self.allow_multiple_selected)
        return context


class LibraryFileInput(LibraryInputMixin, forms.FileInput):
    pass


class LibraryClearableFileInput(LibraryInputMixin, forms.ClearableFileInput):
    native_template = 'django/forms/widgets/clearable_file_input.html'


def library_widget(widget):
    if isinstance(widget, LibraryInputMixin):
        return widget
    kind = LibraryClearableFileInput if isinstance(widget, forms.ClearableFileInput) else LibraryFileInput
    return kind(attrs=widget.attrs.copy())
