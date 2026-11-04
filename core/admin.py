from django.contrib import admin
from django import forms
from django.db import transaction
from .models import Category, Product, ProductImage


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
	list_display = ("name", "slug")
	prepopulated_fields = {"slug": ("name",)}


class ProductImageInline(admin.TabularInline):
	model = ProductImage
	extra = 1


class HistoryProductAdminForm(forms.ModelForm):
    history_token = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Product
        fields = '__all__'


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
	list_display = ("name", "price", "category", "is_featured", "is_published", "sizes", "created_at")
	list_filter = ("category", "is_featured", "is_published")
	search_fields = ("name", "description")
	prepopulated_fields = {"slug": ("name",)}
	inlines = [ProductImageInline]
	form = HistoryProductAdminForm

	@transaction.atomic
	def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
		# GET must render the same state that its editor token signs. POST uses
		# the same Product-first order as the storefront catalogue editor.
		if object_id and str(object_id).isdecimal():
			Product.objects.select_for_update().filter(pk=object_id).first()
		return super().changeform_view(request, object_id, form_url, extra_context)

	def get_form(self, request, obj=None, **kwargs):
		base = super().get_form(request, obj, **kwargs)
		class ProductForm(base):
			def __init__(self, *args, **form_kwargs):
				super().__init__(*args, **form_kwargs)
				if not self.is_bound and self.instance.pk:
					from historique.products import editor_token
					self.initial['history_token'] = editor_token(self.instance, request.user)

			def clean(self):
				cleaned = super().clean()
				if self.instance.pk:
					from historique.products import validate_editor_token
					from historique.exceptions import HistoryError
					product = Product.objects.select_for_update().get(pk=self.instance.pk)
					try:
						validate_editor_token(product, request.user, cleaned.get('history_token', ''))
					except HistoryError as error:
						raise forms.ValidationError(str(error)) from error
				return cleaned
		return ProductForm

	def save_model(self, request, obj, form, change):
		from historique.products import snapshot_product
		if change:
			Product.objects.select_for_update().get(pk=obj.pk)
		request._product_history_before = snapshot_product(obj) if change else None
		super().save_model(request, obj, form, change)

	def save_related(self, request, form, formsets, change):
		super().save_related(request, form, formsets, change)
		from historique.products import record_change
		record_change(form.instance, request._product_history_before, actor=request.user,
			label='Fiche enregistrée dans l’administration')

	@transaction.atomic
	def delete_model(self, request, obj):
		from historique.products import record_product
		product = Product.objects.select_for_update().get(pk=obj.pk)
		record_product(product, actor=request.user, source='checkpoint', label='État conservé avant suppression')
		super().delete_model(request, obj)

	@transaction.atomic
	def delete_queryset(self, request, queryset):
		from historique.products import record_product
		for product in queryset.select_for_update().order_by('pk'):
			record_product(product, actor=request.user, source='checkpoint', label='État conservé avant suppression')
		super().delete_queryset(request, queryset)

# Register your models here.
