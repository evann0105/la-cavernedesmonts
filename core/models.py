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
	name = models.CharField(max_length=200)
	slug = models.SlugField(max_length=220, unique=True)
	price = models.DecimalField(max_digits=10, decimal_places=2)
	description = models.TextField(blank=True)
	main_image = models.ImageField(upload_to='products/', blank=True, null=True)
	is_featured = models.BooleanField(default=False)
	static_image = models.CharField(max_length=500, blank=True)
	sizes = models.CharField(max_length=200, blank=True, help_text='Tailles vendables séparées par une virgule ; utiliser Taille unique si applicable.')
	created_at = models.DateTimeField(auto_now_add=True)

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
			self.slug = slugify(self.name)[:220]
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
