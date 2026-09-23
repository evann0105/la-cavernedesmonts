from django.db import models
from django.utils.text import slugify


class Category(models.Model):
	name = models.CharField(max_length=120)
	slug = models.SlugField(max_length=140, unique=True)

	class Meta:
		verbose_name_plural = 'Categories'

	def __str__(self):
		return self.name


class Product(models.Model):
	category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name='products')
	collections = models.ManyToManyField(Category, blank=True, related_name='collection_products', help_text='Rubriques complémentaires : bébé, offres spéciales…')
	source_url = models.URLField(max_length=500, unique=True, null=True, blank=True, editable=False)
	name = models.CharField(max_length=200)
	slug = models.SlugField(max_length=220, unique=True)
	price = models.DecimalField(max_digits=10, decimal_places=2)
	description = models.TextField(blank=True)
	benefits = models.TextField(blank=True, null=True, default=None, verbose_name='Pourquoi le choisir ?', help_text='Un bénéfice vérifié par ligne, fondé sur les caractéristiques de cet article.')
	translations = models.JSONField(default=dict, blank=True)
	main_image = models.ImageField(upload_to='products/', blank=True, null=True)
	is_featured = models.BooleanField(default=False)
	is_published = models.BooleanField(default=True)
	include_imported_gallery = models.BooleanField(default=True)
	static_image = models.CharField(max_length=500, blank=True)
	sizes = models.CharField(max_length=200, blank=True, help_text='Tailles vendables séparées par une virgule ; utiliser Taille unique si applicable.')
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		permissions = [('manage_catalog', 'Gérer le catalogue depuis la boutique')]

	def translated_value(self, field):
		from django.utils.translation import get_language
		language = (get_language() or 'fr').split('-')[0]
		return self.translations.get(language, {}).get(field) or getattr(self, field)

	@property
	def localized_name(self):
		return self.translated_value('name')

	@property
	def localized_description(self):
		return self.translated_value('description')

	@property
	def description_language(self):
		from django.utils.translation import get_language
		language = (get_language() or 'fr').split('-')[0]
		return language if self.translations.get(language, {}).get('description') else 'fr'

	@property
	def benefit_lines(self):
		from .benefits import catalog_benefits
		if self.benefits is None:
			return catalog_benefits(self)
		if not self.benefits.strip():
			return []
		return [line.strip() for line in self.translated_value('benefits').splitlines() if line.strip()]

	@property
	def benefits_language(self):
		from django.utils.translation import get_language
		language = (get_language() or 'fr').split('-')[0]
		return language if self.benefits is None or self.translations.get(language, {}).get('benefits') else 'fr'

	@property
	def size_options(self):
		return [s.strip() for s in self.sizes.split(',') if s.strip()]

	@property
	def image_url(self):
		from django.templatetags.static import static
		return self.main_image.url if self.main_image else (static(self.static_image) if self.static_image else '')

	def __str__(self):
		return self.name

	def save(self, *args, **kwargs):
		if not self.slug:
			from uuid import uuid4
			base = slugify(self.name)[:200] or 'produit'
			self.slug = base
			while Product.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
				self.slug = f'{base}-{uuid4().hex[:10]}'
		super().save(*args, **kwargs)


class ProductImage(models.Model):
	product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
	image = models.ImageField(upload_to='products/')
	alt = models.CharField(max_length=200, blank=True)
	order = models.PositiveIntegerField(default=0)

	class Meta:
		ordering = ['order', 'id']

	def __str__(self):
		return f"Image for {self.product.name}"


class HomepageSelection(models.Model):
    slot = models.CharField(max_length=20, unique=True, choices=[('femmes', 'Femme'), ('hommes', 'Homme'), ('enfants', 'Enfant')])
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL, related_name='homepage_selections')

    def __str__(self):
        return self.get_slot_display()
