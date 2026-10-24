"""Bounded home slideshow forms and an audited, optimistic save operation."""
from copy import deepcopy

from django import forms
from django.core.files.base import File
from django.db import transaction
from django.db.models.fields.files import FieldFile
from django.forms import BaseModelFormSet, modelformset_factory

from core.catalog_forms import MultiPhotoField, ShopImageField
from .configuration import configuration as current_configuration
from .models import HeroSlide
from .services import (HomepageError, _authorized, _locked, _revision, _save,
                       _validate)


INTERVAL_CHOICES = tuple((seconds, f'{seconds} secondes') for seconds in (5, 6, 8, 10))
DEFAULT_ALT = 'Photo d’ambiance de l’accueil'


class SlideshowPhotoField(MultiPhotoField):
    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        if len(files) > 5:
            raise forms.ValidationError('Ajoutez au maximum cinq photos supplémentaires au diaporama.')
        return super().clean(files, initial)


class SlidesForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    autoplay = forms.BooleanField(label='Faire défiler les photos automatiquement', required=False,
        help_text='Les visiteurs peuvent toujours mettre le défilement en pause. Il reste désactivé lorsque leur appareil demande moins d’animations.')
    interval = forms.TypedChoiceField(label='Temps entre deux photos', choices=INTERVAL_CHOICES, coerce=int)
    new_photos = SlideshowPhotoField(label='Ajouter des photos au diaporama', required=False,
        help_text='La photo principale reste toujours première. Ajoutez jusqu’à cinq photos après elle : JPEG, PNG ou WebP, 10 Mo et 64 mégapixels maximum par photo. Vous pourrez ensuite préciser leur description et leur cadrage mobile.')

    def __init__(self, data=None, files=None, *, configuration=None, **kwargs):
        current = current_configuration() if configuration is None else configuration
        slideshow = current.get('slideshow', {'autoplay': True, 'interval': 6})
        initial = {'revision': current['revision'], **slideshow}
        initial.update(kwargs.pop('initial', {}) or {})
        super().__init__(data, files, initial=initial, **kwargs)


class HeroSlideForm(forms.ModelForm):
    image = ShopImageField(label='Photo', required=True,
        widget=forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'}),
        help_text='Remplacez cette photo ou cochez « Retirer cette photo du diaporama » pour supprimer sa carte.')
    mobile_image = ShopImageField(label='Photo pour téléphone (facultatif)', required=False,
        widget=forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'}),
        help_text='Un cadrage adapté au téléphone. Sans photo mobile, la photo ci-dessus est utilisée.')
    alt = forms.CharField(label='Description de la photo', max_length=200,
        help_text='Décrivez simplement ce que montre la photo. Cette description est lue par les outils d’accessibilité.')
    position = forms.IntegerField(label='Ordre après la photo principale', min_value=1, max_value=5,
        help_text='Choisissez un numéro différent pour chaque photo conservée. Les espaces vides sont regroupés à l’enregistrement.')

    class Meta:
        model = HeroSlide
        fields = ['image', 'mobile_image', 'alt', 'position']


class BaseHeroSlideFormSet(BaseModelFormSet):
    @classmethod
    def get_default_prefix(cls):
        return 'slides'

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('queryset', HeroSlide.objects.filter(configuration_id=1).order_by('position', 'pk'))
        super().__init__(*args, **kwargs)
        for form in self.forms:
            form.fields['DELETE'].label = 'Retirer cette photo du diaporama'

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        seen_positions = set()
        seen_ids = set()
        for form in self.forms:
            row = form.cleaned_data
            instance = row.get('id')
            if instance is None or instance.pk in seen_ids:
                raise forms.ValidationError('La liste des photos est incomplète ou contient un doublon. Rechargez la page.')
            seen_ids.add(instance.pk)
            if row.get('DELETE'):
                continue
            position = row.get('position')
            if position in seen_positions:
                raise forms.ValidationError('Chaque photo conservée doit avoir un numéro d’ordre différent.')
            seen_positions.add(position)


SlideFormSet = modelformset_factory(HeroSlide, form=HeroSlideForm, formset=BaseHeroSlideFormSet,
    extra=0, can_delete=True, max_num=5, validate_max=True, absolute_max=10)


def _replacement(value, existing, *, required):
    """Validate a new upload, or retain precisely this row's current file."""
    if value is False:
        if required:
            raise HomepageError('La photo est obligatoire. Pour retirer une carte, cochez sa case de retrait.')
        return '', False
    if value is None:
        if required and not existing.name:
            raise HomepageError('Chaque carte du diaporama doit contenir une photo.')
        return existing, False
    if isinstance(value, FieldFile):
        if value.name != existing.name:
            raise HomepageError('Une photo d’une autre carte ne peut pas être utilisée sans être ajoutée depuis vos fichiers.')
        if required and not value.name:
            raise HomepageError('Chaque carte du diaporama doit contenir une photo.')
        return existing, False
    if not isinstance(value, File):
        raise HomepageError('Choisissez une photo JPEG, PNG ou WebP valide.')
    try:
        result = ShopImageField(required=required).clean(value)
    except forms.ValidationError as error:
        raise HomepageError(' '.join(error.messages)) from error
    return result, True


def _cleanup_new_files(records):
    for instance, name, previous_name in records:
        image = getattr(instance, name)
        if (image.name and image.name != previous_name and image.name.startswith('homepage/')
                and image._committed):
            image.storage.delete(image.name)


def save_slideshow(cleaned_data, rows, actor):
    """Keep the primary photograph, update extra slides, and never send mail."""
    _authorized(actor)
    expected_revision = _revision(cleaned_data)
    if type(cleaned_data.get('autoplay')) is not bool:
        raise HomepageError('Choisissez si les photos doivent défiler automatiquement.')
    if type(cleaned_data.get('interval')) is not int or cleaned_data['interval'] not in dict(INTERVAL_CHOICES):
        raise HomepageError('Choisissez un intervalle de 5, 6, 8 ou 10 secondes.')
    photos = cleaned_data.get('new_photos', [])
    if photos is None:
        photos = []
    if (not isinstance(photos, (list, tuple)) or len(photos) > 5
            or any(not isinstance(photo, File) or isinstance(photo, FieldFile) for photo in photos)):
        raise HomepageError('Ajoutez au maximum cinq fichiers photo valides.')
    if not isinstance(rows, (list, tuple)) or len(rows) > 10:
        raise HomepageError('La liste des photos est invalide ou trop longue. Rechargez la page.')
    new_files = []
    try:
        with transaction.atomic():
            configuration, is_new = _locked(expected_revision)
            existing = list(HeroSlide.objects.select_for_update().filter(configuration_id=1).order_by('position', 'pk'))
            by_id = {slide.pk: slide for slide in existing}
            seen_ids, seen_positions = set(), set()
            kept, deleted = [], []
            # Validate the entire submitted identity set before changing files
            # or rows; a forged management count must never omit a real slide.
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('id'), HeroSlide):
                    raise HomepageError('La liste des photos est invalide. Rechargez la page.')
                pk = row['id'].pk
                if pk not in by_id or pk in seen_ids or type(row.get('DELETE', False)) is not bool:
                    raise HomepageError('Une photo ne correspond pas au diaporama ou apparaît plusieurs fois. Rechargez la page.')
                seen_ids.add(pk)
                if row.get('DELETE', False):
                    deleted.append(pk)
                    continue
                if type(row.get('position')) is not int or not 1 <= row['position'] <= 5:
                    raise HomepageError('Choisissez un ordre entre 1 et 5 pour chaque photo conservée.')
                if row['position'] in seen_positions:
                    raise HomepageError('Chaque photo conservée doit avoir un numéro d’ordre différent.')
                seen_positions.add(row['position'])
                if not isinstance(row.get('alt'), str):
                    raise HomepageError('Renseignez une description pour chaque photo conservée.')
                try:
                    alt = forms.CharField(max_length=200).clean(row['alt'])
                except forms.ValidationError as error:
                    raise HomepageError('Description de la photo : ' + ' '.join(error.messages)) from error
                kept.append((row['position'], by_id[pk], row, alt))
            if seen_ids != set(by_id):
                raise HomepageError('La liste des photos est incomplète. Rechargez la page avant d’enregistrer.')
            if len(kept) + len(photos) > 5:
                raise HomepageError('Le diaporama accepte cinq photos supplémentaires au maximum. Retirez une photo avant d’en ajouter d’autres.')
            current = current_configuration(instance=configuration)
            validated = _validate(SlidesForm(
                {'revision': expected_revision, 'autoplay': cleaned_data['autoplay'], 'interval': cleaned_data['interval']},
                {'new_photos': photos}, configuration=current))
            staged = []
            for _, slide, row, alt in sorted(kept, key=lambda item: (item[0], item[1].pk)):
                for name, required in (('image', True), ('mobile_image', False)):
                    previous = getattr(slide, name)
                    replacement, uploaded = _replacement(row.get(name), previous, required=required)
                    if uploaded:
                        new_files.append((slide, name, previous.name))
                    setattr(slide, name, replacement)
                slide.alt = alt
                staged.append(slide)
            for photo in validated['new_photos']:
                slide = HeroSlide(configuration=configuration, image=photo, alt=DEFAULT_ALT)
                new_files.append((slide, 'image', ''))
                staged.append(slide)
            data = deepcopy(configuration.data) if isinstance(configuration.data, dict) else {}
            data['slideshow'] = {'autoplay': validated['autoplay'], 'interval': validated['interval']}
            configuration.data = data
            _save(configuration, is_new, actor, 'Diaporama de la grande introduction')
            if deleted:
                HeroSlide.objects.filter(configuration_id=1, pk__in=deleted).delete()
            # The parent singleton lock serializes permutations. Compaction is
            # performed only after validating distinct requested positions.
            for position, slide in enumerate(staged, 1):
                slide.position = position
                slide.full_clean(validate_unique=False, validate_constraints=False)
                slide.save()
            return configuration
    except Exception:
        _cleanup_new_files(new_files)
        raise
