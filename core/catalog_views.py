from django.contrib import messages
from django.contrib.admin.models import LogEntry, ADDITION, CHANGE
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Max
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from .catalog_access import catalog_required
from .catalog_forms import CatalogProductForm, GalleryFormSet
from .models import Category, Product, ProductImage
from .views import product_detail


@catalog_required
@require_http_methods(['GET'])
def catalog_list(request):
    products = Product.objects.select_related('category').order_by('-created_at', '-pk')
    query = request.GET.get('q', '').strip()[:100]
    category = request.GET.get('categorie', '')
    status = request.GET.get('statut', '')
    if query:
        products = products.filter(Q(name__icontains=query) | Q(description__icontains=query))
    if category:
        products = products.filter(Q(category__slug=category) | Q(collections__slug=category)).distinct()
    if status in ('published', 'draft'):
        products = products.filter(is_published=status == 'published')
    return render(request, 'core/catalog/list.html', {'page_obj':Paginator(products, 20).get_page(request.GET.get('page')), 'categories':Category.objects.all(), 'query':query, 'selected':category, 'status':status, 'total_count':Product.objects.count(), 'published_count':Product.objects.filter(is_published=True).count(), 'draft_count':Product.objects.filter(is_published=False).count()})


@catalog_required
@require_http_methods(['GET', 'POST'])
def catalog_edit(request, pk=None):
    product = get_object_or_404(Product, pk=pk) if pk else Product(is_published=False)
    form = CatalogProductForm(request.POST if request.method == 'POST' else None, request.FILES or None, instance=product)
    photos = GalleryFormSet(request.POST if request.method == 'POST' else None, request.FILES or None, instance=product, prefix='photos')
    if request.method == 'POST':
        valid_form, valid_photos = form.is_valid(), photos.is_valid()
        if valid_form and valid_photos:
            remaining = sum(1 for photo in photos.forms if not photo.cleaned_data.get('DELETE'))
            if remaining + len(form.cleaned_data['new_photos']) > 50:
                form.add_error('new_photos', 'La galerie peut contenir au maximum 50 photos.')
                valid_form = False
        if valid_form and valid_photos:
            with transaction.atomic():
                product = form.save()
                photos.instance = product
                photos.save()
                order = product.images.aggregate(last=Max('order'))['last'] or 0
                for index, image in enumerate(form.cleaned_data['new_photos'], start=1):
                    ProductImage.objects.create(product=product, image=image, alt=product.name, order=order+index)
                LogEntry.objects.log_actions(user_id=request.user.pk, queryset=Product.objects.filter(pk=product.pk), action_flag=CHANGE if pk else ADDITION, change_message='Produit enregistré depuis la gestion de la boutique.')
            messages.success(request, 'Produit enregistré. Il est visible dans la boutique.' if product.is_published else 'Produit enregistré en brouillon. Il reste invisible aux clients.')
            return redirect('core:catalog_edit', pk=product.pk)
        messages.error(request, 'Vérifiez les champs signalés. Si vous aviez choisi des fichiers, sélectionnez-les à nouveau avant d’enregistrer.')
    return render(request, 'core/catalog/edit.html', {'form':form, 'photos':photos, 'product':product})


@catalog_required
@require_http_methods(['GET'])
def catalog_preview(request, pk):
    product = get_object_or_404(Product, pk=pk)
    return product_detail(request, product.slug, catalog_preview=True)
