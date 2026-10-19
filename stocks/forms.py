from decimal import Decimal
from django import forms
from .models import Variant
from .adjustments import MAX_QUANTITY
from .identity import identity_change_error


class AdjustmentForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    operation = forms.ChoiceField(label='Action', choices=[
        ('add', 'Ajouter des unités'), ('remove', 'Retirer des unités'),
        ('set', 'Corriger le comptage : fixer la quantité totale'),
    ])
    amount = forms.IntegerField(label='Nombre d’unités', min_value=0, max_value=MAX_QUANTITY,
        widget=forms.NumberInput(attrs={'inputmode':'numeric'}),
        help_text='Ajouter ou retirer : nombre d’unités du mouvement. Corriger le comptage : quantité physique totale, réservations incluses.')
    reason = forms.CharField(label='Motif (facultatif)', required=False, max_length=200,
        widget=forms.TextInput(attrs={'placeholder':'Ex. réception fournisseur, vente en boutique, article abîmé…'}))

    def __init__(self, *args, variant, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['revision'].initial = variant.revision
        self.fields['operation'].initial = 'set' if variant.quantity is None else 'add'

    def clean(self):
        values = super().clean()
        if 'revision' in self.errors:
            self.add_error(None, 'Rechargez les quantités avant de modifier le stock.')
        if values.get('operation') in ('add', 'remove') and values.get('amount') == 0:
            self.add_error('amount', 'Indiquez au moins une unité à ajouter ou retirer.')
        return values


def variant_price_field():
    return forms.DecimalField(
        label='Prix pour cette taille (€)', max_digits=10, decimal_places=2,
        min_value=Decimal('0.01'), required=False, localize=True,
        widget=forms.TextInput(attrs={'inputmode': 'decimal', 'placeholder': 'Prix général'}),
        help_text='Facultatif. Vide : utilise le prix général du produit.',
    )


class VariantForm(forms.ModelForm):
    price = variant_price_field()
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput, initial=0)
    quantity = forms.IntegerField(label='Quantité en stock', min_value=0, max_value=1000000, required=False, help_text='Vide : à compter. Zéro : rupture de stock.')

    class Meta:
        model = Variant
        fields = ['size', 'color', 'reference', 'quantity', 'price']
        help_texts = {'size':'Exemple : M, 6 mois ou Taille unique.', 'color':'Exemple : Bleu ou Coloris unique.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['revision'].initial = self.instance.revision
        if self.instance.pk and self.is_bound and self.add_prefix('price') not in self.data:
            self.data = self.data.copy()
            self.data[self.add_prefix('price')] = self.instance.price if self.instance.price is not None else ''

    def clean(self):
        data = super().clean()
        if 'revision' in self.errors:
            self.add_error(None, 'Rechargez la page avant d’enregistrer la variante.')
        for name in ('size', 'color', 'reference'):
            if name in data:
                data[name] = ' '.join(data[name].split())
        if self.instance.pk:
            original = Variant.objects.filter(pk=self.instance.pk).first()
            if original and data.get('size') and data.get('color'):
                error = identity_change_error(original, data['size'], data['color'])
                if error:
                    raise forms.ValidationError(error)
        if data.get('size') and data.get('color'):
            duplicate = Variant.objects.filter(product=self.instance.product, size__iexact=data['size'], color__iexact=data['color']).exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise forms.ValidationError('Cette taille et cette couleur existent déjà pour ce produit.')
        return data


class InitialVariantForm(forms.Form):
    price = variant_price_field()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['price'].help_text = 'Vide : reprend le prix du coloris s’il est renseigné, sinon le prix général du produit.'
    size = forms.CharField(label='Taille', max_length=50, required=False,
                           widget=forms.TextInput(attrs={'placeholder': 'Taille unique'}))
    color = forms.CharField(label='Couleur', max_length=80, required=False,
                            widget=forms.TextInput(attrs={'placeholder': 'Coloris unique'}))
    reference = forms.CharField(label='Référence interne (facultatif)', max_length=80, required=False)
    quantity = forms.IntegerField(label='Quantité en stock', min_value=0, max_value=1000000,
                                  required=False, widget=forms.NumberInput(attrs={'inputmode': 'numeric'}))

    def clean(self):
        data = super().clean()
        if self.has_changed():
            if any(separator in data.get('size', '') for separator in (',', ';', '\n')):
                self.add_error('size', 'Indiquez une seule taille sur cette ligne. Pour M, L, XL, utilisez le champ « Tailles disponibles pour cette couleur » au-dessus.')
            for name, default in [('size', 'Taille unique'), ('color', 'Coloris unique'), ('reference', '')]:
                data[name] = ' '.join(data.get(name, '').split()) or default
            if data.get('quantity') is None and 'quantity' not in self.errors:
                self.add_error('quantity', 'Indiquez une quantité, même zéro pour une rupture de stock.')
        return data


class InitialStockFormSet(forms.BaseFormSet):
    def __init__(self, *args, product=None, **kwargs):
        self.product = product
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        combinations = set()
        if self.product is not None and self.product.pk:
            combinations = {(' '.join(size.split()).casefold(), ' '.join(color.split()).casefold()) for size, color in self.product.stock_variants.values_list('size', 'color')}
        for form in self.forms:
            if not form.has_changed() or self._should_delete_form(form):
                continue
            key = (form.cleaned_data['size'].casefold(), form.cleaned_data['color'].casefold())
            if key in combinations:
                raise forms.ValidationError('Cette taille et cette couleur existent déjà. Regroupez leurs quantités sur une ligne ou modifiez le stock existant dans l’inventaire.')
            combinations.add(key)


InitialStockForms = forms.formset_factory(
    InitialVariantForm, formset=InitialStockFormSet, extra=1,
    can_delete=True, max_num=50, absolute_max=50, validate_max=True,
)


class VariantPriceForm(forms.Form):
    variant_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    price = variant_price_field()


class VariantPriceFormSet(forms.BaseFormSet):
    """Edit prices only: quantities and purchase snapshots are never rewritten."""
    def __init__(self, *args, product, variants=None, **kwargs):
        self.variants = {variant.pk: variant for variant in (variants if variants is not None else product.stock_variants.all())}
        super().__init__(*args, **kwargs)
        for form in self.forms:
            identifier = form.data.get(form.add_prefix('variant_id')) if form.is_bound else form.initial.get('variant_id')
            try:
                form.variant = self.variants.get(int(identifier))
            except (TypeError, ValueError):
                form.variant = None
            if form.is_bound and form.variant is not None and form.add_prefix('price') not in form.data:
                form.data = form.data.copy()
                form.data[form.add_prefix('price')] = form.variant.price if form.variant.price is not None else ''

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        seen = set()
        for form in self.forms:
            if not form.cleaned_data:
                continue
            identifier = form.cleaned_data['variant_id']
            variant = self.variants.get(identifier)
            if variant is None or identifier in seen:
                raise forms.ValidationError('Une déclinaison est invalide ou ne fait pas partie de ce produit. Rechargez la page.')
            seen.add(identifier)
            if form.cleaned_data['revision'] != variant.revision:
                form.add_error(None, 'Cette déclinaison a changé dans un autre onglet. Rechargez la page avant de modifier son prix.')


VariantPriceForms = forms.formset_factory(
    VariantPriceForm, formset=VariantPriceFormSet, extra=0,
    max_num=1000, absolute_max=1000, validate_max=True,
)
