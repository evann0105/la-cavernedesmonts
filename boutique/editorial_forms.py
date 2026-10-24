"""Small forms for editorial text, photos, questions and dated banners."""
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from django import forms
from django.utils import timezone

from core.catalog_forms import ShopImageField
from .editorial_configuration import page_content
from .editorial_schema import (ANNOUNCEMENT_TARGET_CHOICES, KIND_CHOICES, PAGE_SPECS,
                               PRIORITY_CHOICES, TARGET_CHOICES, plain_text)


@dataclass(frozen=True)
class EditorialImageInitial:
    url: str

    def __str__(self):
        return 'Photo actuellement enregistrée'


class RevisionForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class PlainTextField(forms.CharField):
    def clean(self, value):
        value = super().clean(value)
        return plain_text(value, limit=self.max_length, required=self.required)


class PageContentForm(RevisionForm):
    def __init__(self, key, *args, current=None, **kwargs):
        if key not in PAGE_SPECS:
            raise ValueError('Cette page n’existe pas.')
        current = page_content(key) if current is None else current
        initial = {**current['values'], 'revision': current['revision']}
        if current['image']['custom']:
            initial['image'] = EditorialImageInitial(current['image']['url'])
        initial.update(kwargs.pop('initial', {}) or {})
        super().__init__(*args, initial=initial, **kwargs)
        self.page_key = key
        for name, spec in PAGE_SPECS[key]['fields'].items():
            options = dict(label=spec['label'], max_length=spec['max_length'], required=spec['required'])
            if spec['paragraph']:
                options['widget'] = forms.Textarea(attrs={'rows': 5 if name == 'body' else 3})
                options['help_text'] = 'Texte simple. Séparez les paragraphes par une ligne vide.'
            self.fields[name] = PlainTextField(**options)
        self.fields['image'] = ShopImageField(label='Photo de cette page', required=False,
            widget=forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'}),
            help_text='JPEG, PNG ou WebP : 10 Mo et 64 mégapixels maximum. La photo est optimisée automatiquement. Retirer la photo rétablit la photo d’origine.')
        self.fields['image_alt'].help_text = 'Décrivez brièvement ce que montre la photo. Ce texte aide aussi les personnes qui ne la voient pas.'


class FAQForm(RevisionForm):
    question = PlainTextField(label='Question', max_length=240)
    answer = PlainTextField(label='Réponse', max_length=4000, widget=forms.Textarea(attrs={'rows': 6}),
                            help_text='Texte simple. Vous pouvez séparer les paragraphes par une ligne vide.')
    target = forms.ChoiceField(label='Ajouter un lien vers une page', choices=TARGET_CHOICES, required=False)
    link_label = PlainTextField(label='Texte du lien', max_length=100, required=False)
    position = forms.IntegerField(label='Ordre d’affichage', min_value=1, max_value=1000,
        help_text='Les plus petits numéros apparaissent en premier. Les questions de même numéro gardent leur ordre de création.')
    enabled = forms.BooleanField(label='Afficher cette question sur le site', required=False)

    def clean(self):
        data = super().clean()
        if bool(data.get('target')) != bool(data.get('link_label')):
            self.add_error('link_label', 'Choisissez une page et un texte pour le lien, ou laissez les deux champs vides.')
        return data


class ParisDateTimeField(forms.DateTimeField):
    def to_python(self, value):
        with timezone.override(ZoneInfo('Europe/Paris')):
            return super().to_python(value)

    def prepare_value(self, value):
        with timezone.override(ZoneInfo('Europe/Paris')):
            return super().prepare_value(value)


class AnnouncementForm(RevisionForm):
    name = PlainTextField(label='Nom pour vous retrouver', max_length=120,
                          help_text='Visible uniquement dans l’administration. Exemple : Vacances de février.')
    kind = forms.ChoiceField(label='Type d’annonce', choices=KIND_CHOICES)
    message = PlainTextField(label='Message affiché aux clients', max_length=300,
        widget=forms.Textarea(attrs={'rows': 3}), help_text='Un message court, visible sur toutes les pages du site pendant la période choisie.')
    starts_at = ParisDateTimeField(label='Début d’affichage',
        input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S'],
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
        help_text='Heure de Paris. Le bandeau apparaît à cet instant.')
    ends_at = ParisDateTimeField(label='Fin d’affichage',
        input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S'],
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
        help_text='Heure de Paris. Le bandeau disparaît automatiquement à cet instant.')
    priority = forms.TypedChoiceField(label='Priorité d’affichage', choices=PRIORITY_CHOICES, coerce=int,
        help_text='Une seule annonce est affichée. La priorité la plus haute passe en premier ; à égalité, le début le plus récent est retenu.')
    target = forms.ChoiceField(label='Ajouter un lien vers une page', choices=ANNOUNCEMENT_TARGET_CHOICES, required=False)
    link_label = PlainTextField(label='Texte du lien', max_length=100, required=False)
    enabled = forms.BooleanField(label='Activer cette annonce aux dates choisies', required=False,
        help_text='Décochez pour préparer un brouillon ou retirer immédiatement le bandeau.')

    def clean(self):
        data = super().clean()
        if data.get('starts_at') and data.get('ends_at') and data['ends_at'] <= data['starts_at']:
            self.add_error('ends_at', 'La fin doit être postérieure au début.')
        if bool(data.get('target')) != bool(data.get('link_label')):
            self.add_error('link_label', 'Choisissez une page et un texte pour le lien, ou laissez les deux champs vides.')
        return data
