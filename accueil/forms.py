"""Bounded editorial forms, independent from layouts, markup and legal content."""
from dataclasses import dataclass

from django import forms
from core.catalog_forms import ShopImageField
from .configuration import configuration as current_configuration, eligible_featured_products
from .schema import IMAGE_LABELS, OPTIONAL_SECTIONS, SECTIONS, TARGET_CHOICES


@dataclass(frozen=True)
class HomepageImageInitial:
    url: str

    def __str__(self):
        return 'Photo actuellement enregistrée'


class RevisionForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class SectionForm(RevisionForm):
    def __init__(self, section_key, *args, configuration=None, **kwargs):
        if not isinstance(section_key, str) or section_key not in SECTIONS:
            raise ValueError('Cette section de l’accueil n’existe pas.')
        self.section = SECTIONS[section_key]
        current = current_configuration() if configuration is None else configuration
        initial = {**current['values'][section_key], 'revision': current['revision']}
        for name in self.section['images']:
            if current['images'][name]['custom']:
                initial[name] = HomepageImageInitial(current['images'][name]['url'])
        initial.update(kwargs.pop('initial', {}) or {})
        super().__init__(*args, initial=initial, **kwargs)
        for name, spec in self.section['fields'].items():
            options = {'label': spec['label'], 'required': spec['required']}
            if spec['kind'] == 'target':
                self.fields[name] = forms.ChoiceField(choices=TARGET_CHOICES, **options)
            else:
                options['max_length'] = spec['max_length']
                if spec['kind'] == 'textarea':
                    options['widget'] = forms.Textarea(attrs={'rows': 3})
                self.fields[name] = forms.CharField(**options)
        for name in self.section['images']:
            self.fields[name] = ShopImageField(label=IMAGE_LABELS[name], required=False,
                widget=forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'}),
                help_text='JPEG, PNG ou WebP : 10 Mo et 64 mégapixels maximum. La photo est optimisée automatiquement. Retirer la photo rétablit celle d’origine.')
        if section_key == 'hero':
            self.fields['image_alt'].help_text = 'Une seule description pour ordinateur et téléphone : choisissez des photos ou cadrages cohérents.'
        if section_key == 'contact':
            self.fields['button_label'].help_text = 'Laissez vide pour afficher automatiquement le téléphone ou l’e-mail de la boutique selon la destination.'
        self.image_fields = {}
        for name in self.section['images']:
            alt = ('accessories_image_alt' if name == 'family_accessories_image'
                   else 'fleece_image_alt' if name == 'family_fleece_image'
                   else 'gifts_image_alt' if name == 'family_gifts_image' else 'image_alt')
            self.image_fields[name] = {**current['images'][name], 'reset_field': name + '-clear', 'alt_field': alt}
        self.panels = self._panels(section_key)

    def _panels(self, key):
        names = list(self.section['fields'])
        if key == 'reassurance':
            return [{'title': 'Présentation', 'fields': ['aria_label']}] + [
                {'title': title, 'fields': [name for name in names if name.startswith(prefix + '_')]}
                for prefix, title in (('store', 'Le magasin'), ('advice', 'Les conseils'), ('family', 'Toute la famille'))
            ]
        if key == 'family':
            intro = [name for name in names if not name.startswith(('accessories_', 'fleece_', 'gifts_'))]
            return [{'title': 'Introduction', 'fields': intro}] + [
                {'title': title, 'fields': [name for name in names if name.startswith(prefix + '_')] + [image]}
                for prefix, title, image in (
                    ('accessories', 'Carte Accessoires bébé', 'family_accessories_image'),
                    ('fleece', 'Carte Polaires bébé', 'family_fleece_image'),
                    ('gifts', 'Carte Offres spéciales', 'family_gifts_image'),
                )
            ]
        buttons = [name for name in names if name.endswith(('_label', '_target'))
                   and name not in ('aria_label',)]
        photos = [name for name in names if name.endswith('_alt')] + list(self.section['images'])
        texts = [name for name in names if name not in buttons and name not in photos]
        return [panel for panel in (
            {'title': 'Textes', 'fields': texts}, {'title': 'Boutons et liens', 'fields': buttons},
            {'title': 'Photos', 'fields': photos},
        ) if panel['fields']]


class SectionsVisibilityForm(RevisionForm):
    def __init__(self, *args, configuration=None, **kwargs):
        current = current_configuration() if configuration is None else configuration
        initial = {'revision': current['revision'], **current['visible']}
        initial.update(kwargs.pop('initial', {}) or {})
        super().__init__(*args, initial=initial, **kwargs)
        for key in OPTIONAL_SECTIONS:
            self.fields[key] = forms.BooleanField(label=SECTIONS[key]['title'], required=False,
                help_text=SECTIONS[key]['description'])


class FeaturedProductsForm(RevisionForm):
    def __init__(self, *args, configuration=None, **kwargs):
        current = current_configuration() if configuration is None else configuration
        initial = {'revision': current['revision']}
        initial.update({f'position_{position}': pk for position, pk in enumerate(current['featured_ids'], 1)})
        initial.update(kwargs.pop('initial', {}) or {})
        super().__init__(*args, initial=initial, **kwargs)
        for position in range(1, 5):
            self.fields[f'position_{position}'] = forms.ModelChoiceField(
                label=f'Produit n° {position}', queryset=eligible_featured_products(), required=False,
                empty_label='Aucun choix à cette position',
                help_text='Produits publiés avec une photo. Laissez les quatre positions vides pour retrouver la sélection automatique.')

    def clean(self):
        data = super().clean()
        chosen = set()
        for position in range(1, 5):
            name = f'position_{position}'
            product = data.get(name)
            if product is not None:
                if product.pk in chosen:
                    self.add_error(name, 'Ce produit est déjà choisi à une autre position.')
                chosen.add(product.pk)
        return data
