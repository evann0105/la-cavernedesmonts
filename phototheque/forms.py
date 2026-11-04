from django import forms

from .models import Folder
from .services import MAX_BYTES, MAX_UPLOADS


class MultiplePhotoInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class UploadFilesField(forms.FileField):
    widget = MultiplePhotoInput(attrs={'accept': 'image/jpeg,image/png,image/webp'})

    def clean(self, data, initial=None):
        files = list(data) if isinstance(data, (list, tuple)) else [data] if data else []
        if not 1 <= len(files) <= MAX_UPLOADS:
            raise forms.ValidationError('Choisissez entre 1 et 12 photos à ajouter.')
        cleaned = [super(UploadFilesField, self).clean(item) for item in files]
        if any(item.size > MAX_BYTES for item in cleaned):
            raise forms.ValidationError('Chaque photo doit peser moins de 10 Mo.')
        # The service checks the actual image content and optimizes it once.
        # Keep the original upload name for the library title and search.
        return cleaned


class FolderChoiceField(forms.ModelChoiceField):
    def to_python(self, value):
        if value not in self.empty_values:
            try:
                if len(str(value)) > 19 or not 0 < int(value) <= 9223372036854775807:
                    raise ValueError
            except (ValueError, TypeError, OverflowError):
                raise forms.ValidationError('Choisissez un dossier existant.') from None
        return super().to_python(value)


def folder_field():
    return FolderChoiceField(queryset=Folder.objects.all(), required=False, label='Dossier',
                             empty_label='Non classées')


class UploadPhotosForm(forms.Form):
    photos = UploadFilesField(label='Photos à ajouter', help_text='12 photos maximum par ajout. JPEG, PNG ou WebP : 10 Mo et 64 mégapixels maximum par photo.')
    folder = folder_field()


class PhotoForm(forms.Form):
    title = forms.CharField(label='Nom de la photo', max_length=160,
        help_text='Un nom facile à retrouver, par exemple « Polaire verte — face ». Ce nom sert au rangement dans la bibliothèque.')
    folder = folder_field()


class FolderForm(forms.Form):
    name = forms.CharField(label='Nom du dossier', max_length=80,
        widget=forms.TextInput(attrs={'placeholder': 'Ex. Collection hiver'}))


class PhotoFilterForm(forms.Form):
    q = forms.CharField(label='Rechercher une photo', max_length=160, required=False,
        widget=forms.TextInput(attrs={'type': 'search', 'placeholder': 'Nom de la photo, fichier ou dossier…'}))
    folder = forms.ChoiceField(label='Dossier', required=False)
    state = forms.ChoiceField(label='Photos affichées', required=False,
        choices=[('active', 'Photos disponibles'), ('archived', 'Photos archivées')])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['folder'].choices = [('', 'Tous les dossiers'), ('unfiled', 'Non classées')] + [
            (str(folder.pk), folder.name) for folder in Folder.objects.all()]

    def clean_state(self):
        return self.cleaned_data['state'] or 'active'


class PhotoActionForm(forms.Form):
    confirmed = forms.BooleanField(label='Je confirme cette action.')


class PhotoSelectionField(forms.Field):
    widget = forms.MultipleHiddenInput

    def clean(self, value):
        values = value if isinstance(value, (list, tuple)) else [value] if value else []
        if not 1 <= len(values) <= 24:
            raise forms.ValidationError('Sélectionnez entre 1 et 24 photos à ranger.')
        try:
            if any(len(str(item)) > 19 for item in values):
                raise ValueError
            selected = list(dict.fromkeys(int(item) for item in values))
            if any(not 0 < item <= 9223372036854775807 for item in selected):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise forms.ValidationError('La sélection de photos est invalide.') from None
        return selected


class MovePhotosForm(forms.Form):
    photo_ids = PhotoSelectionField()
    folder = folder_field()
