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
    main_image = ShopImageField(label='Photo principale', required=False, widget=forms.ClearableFileInput(attrs={'accept':'image/jpeg,image/png,image/webp'}), help_text='JPEG, PNG ou WebP, 10 Mo maximum. La photo est optimisée automatiquement.')
    new_photos = MultiPhotoField(label='Ajouter des photos à la galerie', required=False, help_text='Vous pouvez sélectionner plusieurs photos (12 maximum par enregistrement).')
    price = forms.DecimalField(label='Prix TTC (€)', max_digits=10, decimal_places=2, min_value=0.01, localize=True, widget=forms.TextInput(attrs={'inputmode':'decimal', 'placeholder':'39,90'}))
    category = forms.ModelChoiceField(label='Rubrique principale', queryset=Category.objects.all())
    collections = forms.ModelMultipleChoiceField(label='Afficher aussi dans', queryset=Category.objects.all(), required=False, widget=forms.CheckboxSelectMultiple, help_text='Un produit peut figurer dans plusieurs rubriques, par exemple Enfants et Polaires bébé.')
    remove_original = forms.BooleanField(label='Retirer la photo principale d’origine', required=False)

    class Meta:
        model = Product
        fields = ['name', 'price', 'description', 'category', 'collections', 'sizes', 'is_published', 'is_featured', 'main_image', 'include_imported_gallery']
        labels = {'name':'Nom du produit', 'description':'Description et conseils', 'sizes':'Tailles disponibles', 'is_published':'Visible dans la boutique', 'is_featured':'Mettre en avant sur l’accueil', 'include_imported_gallery':'Conserver la galerie de photos d’origine'}
        help_texts = {'sizes':'Séparez les tailles par des virgules : S, M, L ou 6 mois, 12 mois. Pour un accessoire : Taille unique. Indiquez uniquement les tailles disponibles.', 'is_published':'Décochez pour garder un brouillon ou retirer temporairement cet article de la vente.', 'is_featured':'Les quatre premiers articles mis en avant sont affichés sur l’accueil.'}
        widgets = {'description': forms.Textarea(attrs={'rows':7}), 'sizes':forms.TextInput(attrs={'placeholder':'S, M, L ou Taille unique'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
        if data.get('is_published'):
            if not data.get('description', '').strip():
                self.add_error('description', 'Ajoutez une description avant de publier.')
            has_original = self.instance.static_image and not data.get('remove_original')
            if not data.get('main_image') and not has_original:
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
