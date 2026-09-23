from io import BytesIO
from uuid import uuid4
from PIL import Image, ImageOps, UnidentifiedImageError
from django import forms
from django.core.files.base import ContentFile
from django.forms import inlineformset_factory, BaseInlineFormSet
from .models import Category, Product, ProductImage


class ShopImageField(forms.ImageField):
    def to_python(self, data):
        if not data or not hasattr(data, 'read'):
            return data
        if data.size > 10 * 1024 * 1024:
            raise forms.ValidationError('Chaque photo doit peser moins de 10 Mo.')
        try:
            image = Image.open(data)
            if image.format not in ('JPEG', 'PNG', 'WEBP'):
                raise forms.ValidationError('Utilisez une photo JPEG, PNG ou WebP.')
            if image.width * image.height > 24_000_000:
                raise forms.ValidationError('La photo ne doit pas dépasser 24 millions de pixels.')
            image.load()
            image = ImageOps.exif_transpose(image)
            image.thumbnail((2400, 2400))
            image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
            output = BytesIO()
            image.save(output, format='WEBP', quality=88)
            return ContentFile(output.getvalue(), name=f'{uuid4().hex}.webp')
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise forms.ValidationError('Cette photo est illisible. Choisissez un fichier JPEG, PNG ou WebP valide.') from exc
        finally:
            data.seek(0)


class MultiPhotoInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultiPhotoField(ShopImageField):
    widget = MultiPhotoInput(attrs={'accept': 'image/jpeg,image/png,image/webp'})

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        if len(files) > 12:
            raise forms.ValidationError('Ajoutez au maximum 12 photos à la fois.')
        return [super(MultiPhotoField, self).clean(item, initial) for item in files]


class CatalogProductForm(forms.ModelForm):
    benefits = forms.CharField(label='Pourquoi le choisir ?', required=False, max_length=750, widget=forms.Textarea(attrs={'rows':4}), help_text='Un bénéfice concret par ligne, 3 maximum : douceur, coupe, fermeture… Appuyez chaque phrase sur la description ou une fiche fabricant. Ne promettez pas une performance technique ni un entretien sans preuve. Videz ce champ pour masquer le bloc.')
    main_image = ShopImageField(label='Photo principale', required=False, widget=forms.ClearableFileInput(attrs={'accept':'image/jpeg,image/png,image/webp'}), help_text='JPEG, PNG ou WebP, 10 Mo maximum. La photo est optimisée automatiquement.')
    new_photos = MultiPhotoField(label='Ajouter des photos à la galerie', required=False, help_text='Vous pouvez sélectionner plusieurs photos (12 maximum par enregistrement).')
    price = forms.DecimalField(label='Prix TTC (€)', max_digits=10, decimal_places=2, min_value=0.01, localize=True, widget=forms.TextInput(attrs={'inputmode':'decimal', 'placeholder':'39,90'}))
    category = forms.ModelChoiceField(label='Rubrique principale', queryset=Category.objects.all())
    collections = forms.ModelMultipleChoiceField(label='Afficher aussi dans', queryset=Category.objects.all(), required=False, widget=forms.CheckboxSelectMultiple, help_text='Un produit peut figurer dans plusieurs rubriques, par exemple Enfants et Polaires bébé.')
    remove_original = forms.BooleanField(label='Retirer la photo principale d’origine', required=False)

    class Meta:
        model = Product
        fields = ['name', 'price', 'description', 'benefits', 'category', 'collections', 'sizes', 'is_published', 'is_featured', 'main_image', 'include_imported_gallery']
        labels = {'name':'Nom du produit', 'description':'Description et conseils', 'sizes':'Tailles disponibles', 'is_published':'Visible dans la boutique', 'is_featured':'Afficher dans « Les essentiels de la montagne »', 'include_imported_gallery':'Conserver la galerie de photos d’origine'}
        help_texts = {'sizes':'Séparez les tailles par des virgules : S, M, L ou 6 mois, 12 mois. Pour un accessoire : Taille unique. Indiquez uniquement les tailles disponibles.', 'is_published':'Décochez pour garder un brouillon ou retirer temporairement cet article de la vente.', 'is_featured':'Les quatre premiers articles mis en avant sont affichés sur l’accueil.'}
        widgets = {'description': forms.Textarea(attrs={'rows':7}), 'sizes':forms.TextInput(attrs={'placeholder':'S, M, L ou Taille unique'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.benefits is None:
            from django.utils.translation import override
            with override('fr'):
                self.initial['benefits'] = '\n'.join(self.instance.benefit_lines)
        for code, label in [('en','Anglais'), ('de','Allemand'), ('it','Italien'), ('es','Espagnol')]:
            existing = dict(self.instance.translations.get(code, {}))
            if self.instance.benefits is None:
                with override(code):
                    existing['benefits'] = '\n'.join(self.instance.benefit_lines)
            self.fields[f'benefits_{code}'] = forms.CharField(label=f'Pourquoi le choisir ? — {label}', required=False, max_length=750, initial=existing.get('benefits', ''), widget=forms.Textarea(attrs={'rows':3}))
            self.fields[f'name_{code}'] = forms.CharField(label=f'Nom — {label}', max_length=200, required=False, initial=existing.get('name', ''))
            self.fields[f'description_{code}'] = forms.CharField(label=f'Description — {label}', required=False, initial=existing.get('description', ''), widget=forms.Textarea(attrs={'rows':4}))
        if not self.instance.pk:
            self.fields['is_published'].initial = False
            self.initial['is_published'] = False
        if not self.instance.static_image:
            self.fields.pop('remove_original')
        if not self.instance.pk:
            self.fields.pop('include_imported_gallery')

    def clean_sizes(self):
        return ', '.join(dict.fromkeys(s.strip() for s in self.cleaned_data['sizes'].split(',') if s.strip()))

    def clean(self):
        data = super().clean()
        for key in ['benefits'] + [f'benefits_{code}' for code in ('en', 'de', 'it', 'es')]:
            lines = [line.strip() for line in data.get(key, '').splitlines() if line.strip()]
            if len(lines) > 3 or any(len(line) > 250 for line in lines):
                self.add_error(key, 'Indiquez au maximum 3 bénéfices de 250 caractères chacun.')
            else:
                data[key] = '\n'.join(lines)
        if data.get('is_published'):
            if not data.get('description', '').strip():
                self.add_error('description', 'Ajoutez une description avant de publier.')
            has_original = self.instance.static_image and not data.get('remove_original')
            if not data.get('main_image') and not has_original:
                self.add_error('main_image', 'Ajoutez une photo principale avant de publier.')
        return data

    def save(self, commit=True):
        product = super().save(commit=False)
        product.translations = {code: {'name':self.cleaned_data.get(f'name_{code}', ''), 'description':self.cleaned_data.get(f'description_{code}', ''), 'benefits':self.cleaned_data.get(f'benefits_{code}', '')} for code in ('en','de','it','es')}
        if self.cleaned_data.get('remove_original'):
            product.static_image = ''
        if commit:
            product.save()
            self.save_m2m()
        return product


class GalleryPhotoForm(forms.ModelForm):
    image = ShopImageField(label='Remplacer cette photo', required=False, widget=forms.FileInput(attrs={'accept':'image/jpeg,image/png,image/webp'}))

    class Meta:
        model = ProductImage
        fields = ['image', 'alt', 'order']
        labels = {'alt':'Description de la photo', 'order':'Ordre d’affichage'}


class OwnedGalleryFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        for form in self.forms:
            photo = form.cleaned_data.get('id')
            if photo and photo.product_id != self.instance.pk:
                raise forms.ValidationError('Cette photo ne fait pas partie de ce produit.')


GalleryFormSet = inlineformset_factory(Product, ProductImage, form=GalleryPhotoForm, formset=OwnedGalleryFormSet, extra=0, can_delete=True, max_num=50, absolute_max=50, validate_max=True)


class HomepageSelectionForm(forms.Form):
    def __init__(self, *args, **kwargs):
        from .homepage import WORLD_CARDS, eligible_products
        super().__init__(*args, **kwargs)
        for spec in WORLD_CARDS:
            self.fields[spec['slot']] = forms.ModelChoiceField(
                label=f"Carte {spec['label']}", queryset=eligible_products(spec['slot']),
                required=False, empty_label='Choix automatique',
                help_text='Produits publiés avec une photo, dans cette rubrique. La photo principale sera utilisée.',
            )
