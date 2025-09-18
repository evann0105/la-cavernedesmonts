from django.contrib import admin
from .models import Category, Product, ProductImage


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
	list_display = ("name", "slug")
	prepopulated_fields = {"slug": ("name",)}


class ProductImageInline(admin.TabularInline):
	model = ProductImage
	extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
	list_display = ("name", "price", "category", "is_featured", "created_at")
	list_filter = ("category", "is_featured")
	search_fields = ("name", "description")
	prepopulated_fields = {"slug": ("name",)}
	inlines = [ProductImageInline]

# Register your models here.
