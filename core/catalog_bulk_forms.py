"""Explicit opt-in changes for a frozen selection of catalogue products."""
from decimal import Decimal
from django import forms
from django.db import transaction
from .category_forms import category_choice_label

from .models import Category, Product


class CatalogBulkForm(forms.Form):
    change_category = forms.BooleanField(label='Changer la rubrique principale', required=False)
    category = forms.ModelChoiceField(label='Nouvelle rubrique principale', queryset=Category.objects.all(), required=False)
    collections_add = forms.ModelMultipleChoiceField(label='Ajouter dans ces rubriques', queryset=Category.objects.all(), required=False, widget=forms.CheckboxSelectMultiple)
    collections_remove = forms.ModelMultipleChoiceField(label='Retirer de ces rubriques complémentaires', queryset=Category.objects.all(), required=False, widget=forms.CheckboxSelectMultiple)
    change_brand = forms.BooleanField(label='Changer la marque', required=False)
    brand = forms.CharField(label='Nouvelle marque', max_length=100, required=False, help_text='Laissez vide pour retirer la marque des articles sélectionnés.')
    change_article_type = forms.BooleanField(label='Changer le type d’article', required=False)
    article_type = forms.ChoiceField(label='Nouveau type d’article', choices=[('', 'Classement automatique')] + list(Product._meta.get_field('article_type').choices), required=False)
    change_is_published = forms.BooleanField(label='Changer la visibilité', required=False)
    is_published = forms.ChoiceField(label='Nouvelle visibilité', choices=[('', 'Choisir'), ('1', 'Publié dans la boutique'), ('0', 'Brouillon / retiré')], required=False)
    change_is_featured = forms.BooleanField(label='Changer la mise en avant', required=False)
    is_featured = forms.ChoiceField(label='Mise en avant', choices=[('', 'Choisir'), ('1', 'Mettre à la une'), ('0', 'Retirer de la une')], required=False, help_text='Concerne la sélection automatique de l’accueil. Les sélections manuelles de l’accueil restent prioritaires.')
    change_availability = forms.BooleanField(label='Changer la disponibilité indicative', required=False)
    availability = forms.ChoiceField(label='Nouvelle disponibilité indicative', choices=[('', 'Choisir')] + list(Product._meta.get_field('availability').choices), required=False, help_text='Cette indication ne modifie aucune quantité par taille ou couleur.')
    change_price = forms.BooleanField(label='Changer le prix général', required=False)
    price = forms.DecimalField(label='Nouveau prix général (€)', max_digits=10, decimal_places=2, min_value=Decimal('0.01'), required=False, localize=True, widget=forms.TextInput(attrs={'inputmode': 'decimal', 'placeholder': '39,90'}), help_text='Ce prix devient le même pour tous les articles sélectionnés. Les prix spécifiques des tailles et couleurs sont conservés. Les déclinaisons sans prix spécifique suivent ce nouveau prix général.')

    OPTIONAL_FIELDS = ('category', 'brand', 'article_type', 'is_published', 'is_featured', 'availability', 'price')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('category', 'collections_add'):
            self.fields[name].queryset = Category.objects.filter(archived_at__isnull=True)
        self.fields['collections_remove'].label_from_instance = category_choice_label
        # Ignore even malformed values when the corresponding change is not selected.
        if self.is_bound:
            for name in self.OPTIONAL_FIELDS:
                if self.data.get('change_' + name) not in ('1', 'on', 'true', True):
                    self.fields[name].disabled = True

    def clean(self):
        data = super().clean()
        for name in ('category', 'price', 'is_published', 'is_featured', 'availability'):
            if data.get('change_' + name) and data.get(name) in (None, '') and name not in self.errors:
                self.add_error(name, 'Choisissez la nouvelle valeur à appliquer.')
        additions = {item.pk for item in data.get('collections_add', [])}
        removals = {item.pk for item in data.get('collections_remove', [])}
        chosen = additions | ({data['category'].pk} if data.get('change_category') and data.get('category') else set())
        # During apply this shares the outer transaction with the product changes.
        with transaction.atomic():
            rows = list(Category.objects.select_for_update().filter(pk__in=chosen).order_by('pk'))
            if len(rows) != len(chosen) or any(row.archived_at for row in rows):
                raise forms.ValidationError('Une rubrique a été archivée ou supprimée. Rechargez les choix avant de continuer.')
        if additions & removals:
            self.add_error('collections_remove', 'Une rubrique ne peut pas être ajoutée et retirée en même temps.')
        if not any(data.get('change_' + name) for name in self.OPTIONAL_FIELDS) and not additions and not removals:
            raise forms.ValidationError('Choisissez au moins un changement avant de préparer l’aperçu.')
        return data

    def changes(self):
        if not self.is_valid():
            raise forms.ValidationError('Vérifiez les changements demandés.')
        result = {}
        for name in self.OPTIONAL_FIELDS:
            if not self.cleaned_data.get('change_' + name):
                continue
            value = self.cleaned_data[name]
            if name == 'category':
                value = value.pk
            elif name in ('is_published', 'is_featured'):
                value = value == '1'
            elif name == 'price':
                value = str(value)
            result[name] = value
        for name in ('collections_add', 'collections_remove'):
            ids = sorted(item.pk for item in self.cleaned_data.get(name, []))
            if ids:
                result[name] = ids
        return result


def changes_form_data(changes):
    """Build editable form data from the server's signed, normalized changes."""
    data = {}
    for name, value in changes.items():
        if name in CatalogBulkForm.OPTIONAL_FIELDS:
            data['change_' + name] = '1'
            data[name] = ('1' if value else '0') if name in ('is_published', 'is_featured') else value
        elif name in ('collections_add', 'collections_remove'):
            data[name] = value
    return data
