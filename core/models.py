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
	name = models.CharField(max_length=200)
	slug = models.SlugField(max_length=220, unique=True)
	price = models.DecimalField(max_digits=10, decimal_places=2)
	description = models.TextField(blank=True)
	main_image = models.ImageField(upload_to='products/', blank=True, null=True)
	is_featured = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)

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
