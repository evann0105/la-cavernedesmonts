from io import BytesIO
import re
from uuid import uuid4
from PIL import Image, ImageOps, UnidentifiedImageError
from django import forms
from django.core.files.base import ContentFile
from django.forms import inlineformset_factory, BaseInlineFormSet
from .models import Category, Product, ProductImage, ProductColor


class ShopImageField(forms.ImageField):
    MAX_PIXELS = 64_000_000

    def to_python(self, data):
        if not data or not hasattr(data, 'read'):
            return data
        if data.size > 10 * 1024 * 1024:
            raise forms.ValidationError('Chaque photo doit peser moins de 10 Mo.')
        try:
            image = Image.open(data)
            # MPO is a JPEG container used by multi-picture phone photos.
            # Keep only its primary photograph; never trust the filename.
            if image.format not in ('JPEG', 'MPO', 'PNG', 'WEBP'):
                raise forms.ValidationError(
                    'Format détecté : %(format)s. Utilisez une photo JPEG, PNG ou WebP.',
                    params={'format': image.format or 'inconnu'},
                )
            if image.width * image.height > self.MAX_PIXELS:
                raise forms.ValidationError('Cette photo dépasse 64 mégapixels. Exportez une version plus petite avant de l’ajouter.')
            image.seek(0)
            # JPEG/MPO can decode at reduced resolution to limit peak memory.
            image.draft('RGB', (2400, 2400))
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
    main_image = ShopImageField(label='Photo principale', required=False, widget=forms.ClearableFileInput(attrs={'accept':'image/jpeg,image/png,image/webp'}), help_text='JPEG, PNG ou WebP, 10 Mo et 64 mégapixels maximum. La photo est redimensionnée et optimisée automatiquement.')
    new_photos = MultiPhotoField(label='Ajouter des photos à la galerie', required=False, help_text='Vous pouvez sélectionner plusieurs photos (12 maximum par enregistrement). JPEG, PNG ou WebP : 10 Mo et 64 mégapixels maximum par photo.')
    price = forms.DecimalField(label='Prix TTC (€)', max_digits=10, decimal_places=2, min_value=0.01, localize=True, widget=forms.TextInput(attrs={'inputmode':'decimal', 'placeholder':'39,90'}))
    category = forms.ModelChoiceField(label='Rubrique principale', queryset=Category.objects.all())
    collections = forms.ModelMultipleChoiceField(label='Afficher aussi dans', queryset=Category.objects.all(), required=False, widget=forms.CheckboxSelectMultiple, help_text='Un produit peut figurer dans plusieurs rubriques, par exemple Enfants et Polaires bébé.')
    remove_original = forms.BooleanField(label='Retirer la photo principale d’origine', required=False)

    class Meta:
        model = Product
        fields = ['name', 'price', 'brand', 'model_reference', 'composition', 'brand_contact', 'information_review_notes', 'description', 'benefits', 'category', 'collections', 'sizes', 'article_type', 'availability', 'is_published', 'is_featured', 'main_image', 'include_imported_gallery']
        labels = {'name':'Nom du produit', 'description':'Description et conseils', 'sizes':'Tailles (indication générale, facultatif)', 'is_published':'Visible dans la boutique', 'is_featured':'Afficher dans « Les essentiels de la montagne »', 'include_imported_gallery':'Conserver la galerie de photos d’origine'}
        help_texts = {'sizes':'Les déclinaisons ci-dessous définissent les tailles et les couleurs réellement commandables, avec leur stock. Ce champ seul ne crée aucun stock. Pour une fiche sans déclinaison : S, M, L ou Taille unique.', 'is_published':'Décochez pour garder un brouillon ou retirer temporairement cet article de la vente.', 'is_featured':'Les quatre premiers articles mis en avant sont affichés sur l’accueil.'}
        widgets = {'information_review_notes':forms.Textarea(attrs={'rows':4}), 'composition':forms.Textarea(attrs={'rows':4}), 'brand_contact':forms.Textarea(attrs={'rows':4}), 'description': forms.Textarea(attrs={'rows':7}), 'sizes':forms.TextInput(attrs={'placeholder':'S, M, L ou Taille unique'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['availability'].required = False
        if self.instance.benefits is None:
            from django.utils.translation import override
            with override('fr'):
                self.initial['benefits'] = '\n'.join(self.instance.benefit_lines)
        if not self.instance.pk:
            self.fields['is_published'].initial = False
            self.initial['is_published'] = False
        if not self.instance.static_image:
            self.fields.pop('remove_original')
        if not self.instance.pk:
            self.fields.pop('include_imported_gallery')

    def clean_availability(self):
        return self.cleaned_data.get('availability') or self.instance.availability or 'unknown'

    def clean_sizes(self):
        return ', '.join(dict.fromkeys(s.strip() for s in self.cleaned_data['sizes'].split(',') if s.strip()))

    def clean(self):
        data = super().clean()
        for key in ['benefits']:
            lines = [line.strip() for line in data.get(key, '').splitlines() if line.strip()]
            if len(lines) > 3 or any(len(line) > 250 for line in lines):
                self.add_error(key, 'Indiquez au maximum 3 bénéfices de 250 caractères chacun.')
            else:
                data[key] = '\n'.join(lines)
        if data.get('is_published'):
            if not data.get('description', '').strip():
                self.add_error('description', 'Ajoutez une description avant de publier.')
            has_original = self.instance.static_image and not data.get('remove_original')
            if 'main_image' not in self.errors and not data.get('main_image') and not has_original:
                self.add_error('main_image', 'Ajoutez une photo principale avant de publier.')
        return data

    def save(self, commit=True):
        product = super().save(commit=False)
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


class ProductColorForm(forms.ModelForm):
    sizes = forms.CharField(
        label='Tailles disponibles pour cette couleur', required=False, max_length=1000,
        widget=forms.TextInput(attrs={'placeholder': 'M, L, XL', 'autocomplete': 'off'}),
        help_text='Séparez les tailles par des virgules, par exemple M, L, XL ou 6 mois, 12 mois. Préparez ensuite les quantités pour chaque taille.',
    )
    image = ShopImageField(label='Photo de cette couleur', required=True,
        widget=forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'}),
        help_text='JPEG, PNG ou WebP. 10 Mo et 64 mégapixels maximum.')

    class Meta:
        model = ProductColor
        fields = ['name', 'image']
        labels = {'name': 'Nom de la couleur'}
        widgets = {'name': forms.TextInput(attrs={'placeholder': 'Exemple : Rouge'})}

    def clean_sizes(self):
        labels = {}
        for value in re.split(r'[,;\n]+', self.cleaned_data.get('sizes', '')):
            label = ' '.join(value.split())
            if not label:
                continue
            if len(label) > 50:
                raise forms.ValidationError('Chaque taille doit contenir au maximum 50 caractères.')
            labels.setdefault(label.casefold(), label)
        if len(labels) > 50:
            raise forms.ValidationError('Préparez au maximum 50 tailles à la fois.')
        return ', '.join(labels.values())

    @property
    def size_options(self):
        return [label for label in self.cleaned_data.get('sizes', '').split(', ') if label]

    def clean_name(self):
        name = ' '.join(self.cleaned_data['name'].split())
        if not name:
            raise forms.ValidationError('Indiquez une couleur.')
        return name


class ProductColorFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        names = set()
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get('DELETE'):
                continue
            item = form.cleaned_data.get('id')
            if item and item.product_id != self.instance.pk:
                raise forms.ValidationError('Cette couleur ne fait pas partie de ce produit.')
            name = form.cleaned_data['name'].casefold()
            if name in names:
                raise forms.ValidationError('Ajoutez une seule photo par couleur. La même photo servira à toutes ses tailles.')
            names.add(name)


ColorPhotoForms = inlineformset_factory(Product, ProductColor, form=ProductColorForm,
    formset=ProductColorFormSet, extra=1, can_delete=True, max_num=30, absolute_max=30, validate_max=True)
