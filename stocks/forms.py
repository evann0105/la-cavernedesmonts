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


class VariantForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput, initial=0)
    quantity = forms.IntegerField(label='Quantité en stock', min_value=0, max_value=1000000, required=False, help_text='Vide : à compter. Zéro : rupture de stock.')

    class Meta:
        model = Variant
        fields = ['size', 'color', 'reference', 'quantity']
        help_texts = {'size':'Exemple : M, 6 mois ou Taille unique.', 'color':'Exemple : Bleu ou Coloris unique.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['revision'].initial = self.instance.revision

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
            combinations = {(size.casefold(), color.casefold()) for size, color in self.product.stock_variants.values_list('size', 'color')}
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
