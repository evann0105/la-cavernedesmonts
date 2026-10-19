"""Filter a frozen selection, then confirm one scheduled operation."""
from decimal import Decimal

from django import forms

from .models import Category, Product
from .promotion_forms import ProductPromotionForm


class SaleSelectionForm(forms.Form):
    q = forms.CharField(label='Rechercher un article ou une marque', required=False, max_length=200)
    categories = forms.ModelMultipleChoiceField(label='Catégories', queryset=Category.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple)
    article_type = forms.ChoiceField(label='Type d’article', required=False)
    price_min = forms.DecimalField(label='Prix habituel minimum (€)', required=False, min_value=0, max_digits=10, decimal_places=2)
    price_max = forms.DecimalField(label='Prix habituel maximum (€)', required=False, min_value=0, max_digits=10, decimal_places=2)
    color = forms.CharField(label='Couleur', required=False, max_length=80)
    size = forms.CharField(label='Taille', required=False, max_length=50)
    stock = forms.ChoiceField(label='Stock des déclinaisons', choices=[('all', 'Tous les stocks'), ('available', 'En stock'), ('zero', 'Rupture de stock')], initial='all')
    publication = forms.ChoiceField(label='Publication', choices=[('published', 'Articles publiés'), ('all', 'Tous les articles')], initial='published')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['categories'].queryset = Category.objects.order_by('name')
        self.fields['article_type'].choices = [('', 'Tous les types')] + list(Product._meta.get_field('article_type').choices)
        for name in ('price_min', 'price_max'):
            self.fields[name].widget.attrs.update({'step': '0.01', 'min': '0', 'inputmode': 'decimal'})

    def clean(self):
        data = super().clean()
        if data.get('price_min') is not None and data.get('price_max') is not None and data['price_max'] < data['price_min']:
            self.add_error('price_max', 'Le prix maximum doit être supérieur ou égal au prix minimum.')
        return data

    def criteria(self):
        if not self.is_valid():
            raise ValueError('Invalid selection criteria')
        data = self.cleaned_data
        return {'q': data['q'], 'categories': [obj.pk for obj in data['categories']],
            'article_type': data['article_type'], 'price_min': str(data['price_min']) if data['price_min'] is not None else '',
            'price_max': str(data['price_max']) if data['price_max'] is not None else '',
            'color': data['color'], 'size': data['size'], 'stock': data['stock'], 'publication': data['publication']}


class SaleCampaignForm(forms.Form):
    title = forms.CharField(label='Nom de l’opération', max_length=160,
        widget=forms.TextInput(attrs={'placeholder': 'Exemple : Polaires d’hiver'}))
    start_mode = ProductPromotionForm.base_fields['start_mode']
    starts_at = ProductPromotionForm.base_fields['starts_at']
    ends_at = ProductPromotionForm.base_fields['ends_at']
    kind = ProductPromotionForm.base_fields['kind']
    value = ProductPromotionForm.base_fields['value']
    reference_confirmed = ProductPromotionForm.base_fields['reference_confirmed']

    def __init__(self, *args, targets, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_rows = []
        for target in targets:
            name = f"ref_{target['key']}"
            self.fields[name] = forms.DecimalField(label=f"{target['product'].name} — {target['label']}",
                min_value=Decimal('0.01'), max_digits=10, decimal_places=2, initial=target['reference'],
                widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0.01', 'inputmode': 'decimal', 'data-sale-reference': ''}))
            self.target_rows.append({**target, 'field': self[name]})
