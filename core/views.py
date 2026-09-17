from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import render, get_object_or_404, redirect
from django.templatetags.static import static
from django.views.decorators.http import require_POST
from .models import Product, Category
from .cart import lines, total


def product_list(request):
    products = Product.objects.filter(is_published=True).select_related('category').order_by('-is_featured', 'name')
    category = request.GET.get('categorie', '')
    query = request.GET.get('q', '').strip()[:100]
    if category:
        products = products.filter(Q(category__slug=category) | Q(collections__slug=category)).distinct()
    if query:
        products = products.filter(Q(name__icontains=query) | Q(description__icontains=query))
    sort = request.GET.get('tri', '')
    if sort in ('prix', '-prix'):
        products = products.order_by('price' if sort == 'prix' else '-price')
    selected_category = Category.objects.filter(slug=category).first() if category else None
    return render(request, 'core/product_list.html', {'products': products, 'categories': Category.objects.all(), 'selected': category, 'selected_category': selected_category, 'query': query, 'sort': sort})


def product_detail(request, slug, catalog_preview=False):
    products = Product.objects.all() if catalog_preview else Product.objects.filter(is_published=True)
    product = get_object_or_404(products.select_related('category'), slug=slug)
    images = [product.image_url] if product.image_url else []
    images += [i.image.url for i in product.images.all()]
    for folder in (('products', 'best_product', 'femmes', 'hommes', 'enfants') if product.include_imported_gallery else ()):
        directory = Path(settings.BASE_DIR) / 'core/static/core/img' / folder / slug
        if directory.is_dir():
            for p in sorted(directory.iterdir()):
                if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
                    url = static(f'core/img/{folder}/{slug}/{p.name}')
                    if url not in images:
                        images.append(url)
    photo_alts = {photo.image.url: photo.alt for photo in product.images.all()}
    gallery = [{'url': url, 'alt': photo_alts.get(url) or product.name} for url in images]
    related = Product.objects.filter(category=product.category, is_published=True).exclude(pk=product.pk)[:4]
    return render(request, 'core/product_detail.html', {'product': product, 'images': images, 'gallery': gallery, 'related': related, 'catalog_preview': catalog_preview})


def product_preview(request, slug):
    get_object_or_404(Product, slug=slug, is_published=True)
    return redirect('core:product_detail', slug=slug, permanent=True)


def location(request):
    return render(request, 'core/location.html')


def cart(request):
    items = lines(request)
    return render(request, 'core/cart.html', {'cart_items': items, 'total': total(items)})


@require_POST
def cart_add(request, slug):
    product = get_object_or_404(Product, slug=slug, is_published=True)
    size = request.POST.get('size', '').strip()
    # Unconfirmed sizes remain explicit and block payment until merchant review.
    if product.size_options and size not in product.size_options:
        messages.error(request, 'Choisissez une taille disponible.')
        return redirect('core:product_detail', slug=slug)
    if not product.size_options:
        size = 'À confirmer avec la boutique'
    try:
        quantity = int(request.POST.get('quantity', '1'))
        if not 1 <= quantity <= 10:
            raise ValueError
    except (ValueError, TypeError):
        messages.error(request, 'Choisissez une quantité entre 1 et 10.')
        return redirect('core:product_detail', slug=slug)
    cart = request.session.get('cart', {})
    key = f'{product.pk}:{size}'
    cart[key] = {'product': product.pk, 'size': size, 'quantity': min(10, cart.get(key, {}).get('quantity', 0) + quantity)}
    request.session['cart'] = cart
    messages.success(request, 'Un peu de montagne ajouté à votre panier.')
    return redirect('core:cart')


@require_POST
def cart_update(request):
    cart = request.session.get('cart', {})
    key = request.POST.get('key', '')
    try:
        quantity = int(request.POST.get('quantity', '0'))
        if not 0 <= quantity <= 10:
            raise ValueError
    except (ValueError, TypeError):
        messages.error(request, 'La quantité doit être comprise entre 0 et 10.')
        return redirect('core:cart')
    if key in cart:
        if quantity == 0:
            del cart[key]
        else:
            cart[key]['quantity'] = quantity
        request.session['cart'] = cart
    return redirect('core:cart')
