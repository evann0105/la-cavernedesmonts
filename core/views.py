from django.utils.translation import gettext as _, get_language
from pathlib import Path
from django.conf import settings
from django.core.paginator import Paginator
from .product_images import thumbnail_url
from .catalog_filters import with_article_type
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import render, get_object_or_404, redirect
from django.templatetags.static import static
from django.views.decorators.http import require_POST
from .models import Product, Category
from .cart import lines, total


def product_list(request):
    products = Product.objects.filter(is_published=True).select_related('category').order_by('-is_featured', 'name', 'pk')
    products = with_article_type(products)
    category = request.GET.get('categorie', '')
    query = request.GET.get('q', '').strip()[:100]
    if category == 'cadeaux-bebe':
        products = products.filter(Q(category__slug__in=['accessoires-bebe', 'polaires-bebe']) | Q(collections__slug__in=['accessoires-bebe', 'polaires-bebe'])).distinct()
    elif category:
        products = products.filter(Q(category__slug=category) | Q(collections__slug=category)).distinct()
    if query:
        language = (get_language() or 'fr').split('-')[0]
        localized = Q(**{f'translations__{language}__name__icontains':query}) | Q(**{f'translations__{language}__description__icontains':query})
        products = products.filter(Q(name__icontains=query) | Q(description__icontains=query) | localized)
    article_type = request.GET.get('type', '')
    availability = request.GET.get('disponibilite', '')
    size = request.GET.get('taille', '').strip()[:50]
    from django.db.models import Exists, OuterRef, F
    from stocks.models import Variant
    variants = Variant.objects.filter(product_id=OuterRef('pk'))
    legacy_sizes = {item.strip() for csv in products.filter(stock_variants__isnull=True).values_list('sizes', flat=True) for item in csv.split(',') if item.strip()}
    variant_sizes = set(Variant.objects.filter(product__in=products).values_list('size', flat=True))
    size_choices = sorted(legacy_sizes | variant_sizes, key=str.casefold)
    selected_variants = variants.filter(size__iexact=size) if size else variants
    products = products.annotate(
        has_variants=Exists(variants),
        matching_variant=Exists(selected_variants),
        available_variant=Exists(selected_variants.filter(quantity__gt=F('reserved'))),
        unknown_variant=Exists(selected_variants.filter(quantity__isnull=True)),
    )
    if article_type:
        products = products.filter(resolved_type=article_type)
    if size:
        ids = [pk for pk, csv in products.filter(has_variants=False).values_list('pk', 'sizes') if size.casefold() in {item.strip().casefold() for item in csv.split(',')}]
        products = products.filter(Q(matching_variant=True) | Q(pk__in=ids))
    if availability in ('available', 'unavailable', 'unknown'):
        legacy = Q(has_variants=False, availability=availability)
        if availability == 'available':
            tracked = Q(has_variants=True, available_variant=True) & ~Q(availability='unavailable')
        elif availability == 'unknown':
            tracked = Q(has_variants=True, available_variant=False, unknown_variant=True) & ~Q(availability='unavailable')
        else:
            tracked = Q(has_variants=True) & (Q(availability='unavailable') | Q(available_variant=False, unknown_variant=False))
        products = products.filter(legacy | tracked)
    intent_title = ''
    intent_intro = ''
    if category == 'femmes' and article_type == 'polaires':
        intent_title = _('Polaires femme')
        intent_intro = _('Pour les moments au chaud. Comparez les coupes et demandez-nous conseil pour choisir votre taille.')
    elif category == 'hommes' and article_type == 'vestes':
        intent_title = _('Vestes homme')
        intent_intro = _('Trouvez la veste qui accompagne vos sorties. Les caractéristiques de chaque modèle vous aident à choisir.')
    elif category == 'cadeaux-bebe':
        intent_title = _('Cadeaux bébé')
        intent_intro = _('Une petite attention pour les premiers mois. Besoin de choisir une taille ou un cadeau ? Parlons-en.')
    sort = request.GET.get('tri', '')
    if sort in ('prix', '-prix'):
        products = products.order_by('price' if sort == 'prix' else '-price', 'pk')
    selected_category = Category.objects.filter(slug=category).first() if category else None
    page = Paginator(products, 24).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'core/product_list.html', {'products': page, 'page_obj': page, 'pagination_query': params.urlencode(), 'categories': Category.objects.all(), 'selected': category, 'selected_category': selected_category, 'query': query, 'sort': sort, 'article_type': article_type, 'availability': availability, 'size': size, 'size_choices': size_choices, 'type_choices': Product._meta.get_field('article_type').choices, 'availability_choices': Product._meta.get_field('availability').choices, 'intent_title': intent_title, 'intent_intro': intent_intro})


def product_detail(request, slug, catalog_preview=False):
    products = Product.objects.all() if catalog_preview else Product.objects.filter(is_published=True)
    product = get_object_or_404(products.select_related('category').prefetch_related('images', 'color_photos', 'stock_variants'), slug=slug)
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
    color_photos = list(product.color_photos.all())
    photo_alts = {photo.image.url: photo.alt for photo in product.images.all()}
    for color in color_photos:
        if color.image.url not in images:
            images.append(color.image.url)
        photo_alts[color.image.url] = f'{product.localized_name} — {color.name}'
    variants = list(product.stock_variants.all())
    colors = {}
    for variant in variants:
        variant.color_key = variant.color.casefold()
        colors.setdefault(variant.color_key, {'key':variant.color_key, 'name':variant.color, 'image':''})
    for color in color_photos:
        key = color.name.casefold()
        colors[key] = {'key':key, 'name':color.name, 'image':color.image.url}

    gallery = [{'url': url, 'thumbnail': thumbnail_url(url), 'alt': photo_alts.get(url) or product.localized_name} for url in images]
    related = Product.objects.filter(category=product.category, is_published=True).select_related('category').exclude(pk=product.pk)[:4]
    return render(request, 'core/product_detail.html', {'product': product, 'images': images, 'gallery': gallery, 'related': related, 'catalog_preview': catalog_preview,
        'variants':variants, 'product_colors':list(colors.values()),
        'has_available_variant':any(variant.available > 0 for variant in variants),
        'has_unknown_stock':any(variant.quantity is None for variant in variants)})


def product_preview(request, slug):
    get_object_or_404(Product, slug=slug, is_published=True)
    return redirect('core:product_detail', slug=slug, permanent=True)


def location(request):
    return render(request, 'core/location.html')


def cart(request):
    from paiement.shipping import shipping_summary
    items = lines(request)
    subtotal = total(items)
    return render(request, 'core/cart.html', {'cart_items': items, 'total': subtotal, 'shipping': shipping_summary(subtotal)})


@require_POST
def cart_add(request, slug):
    product = get_object_or_404(Product, slug=slug, is_published=True)
    if product.availability == 'unavailable':
        messages.error(request, _('Indisponible'))
        return redirect('core:product_detail', slug=slug)
    from stocks.models import Variant
    variant = None
    if product.stock_variants.exists():
        try:
            variant = product.stock_variants.get(pk=int(request.POST.get('variant','')))
        except (ValueError, TypeError, Variant.DoesNotExist):
            messages.error(request, _('Choisissez une taille et une couleur disponibles.'))
            return redirect('core:product_detail', slug=slug)
        size = variant.size
    else:
        size = request.POST.get('size', '').strip()
        if product.size_options and size not in product.size_options:
            messages.error(request, _('Choisissez une taille disponible.'))
            return redirect('core:product_detail', slug=slug)
        if not product.size_options:size = 'À confirmer avec la boutique'
    try:
        quantity = int(request.POST.get('quantity', '1'))
        if not 1 <= quantity <= 10:
            raise ValueError
    except (ValueError, TypeError):
        messages.error(request, _('Choisissez une quantité entre 1 et 10.'))
        return redirect('core:product_detail', slug=slug)
    if not prepare_cart_change(request):
        return redirect('core:cart')
    if variant:
        variant.refresh_from_db()
    cart = request.session.get('cart', {})
    key = f'variant:{variant.pk}' if variant else f'{product.pk}:{size}'
    if variant and variant.available < min(10, cart.get(key, {}).get('quantity',0)+quantity):
        messages.error(request, _('La quantité demandée dépasse le stock disponible.'))
        return redirect('core:product_detail', slug=slug)
    cart[key] = {'product': product.pk, 'variant':variant.pk if variant else None, 'size':size, 'color':variant.color if variant else '', 'quantity': min(10, cart.get(key, {}).get('quantity', 0) + quantity)}
    request.session['cart'] = cart
    messages.success(request, _('Un peu de montagne ajouté à votre panier.'))
    return redirect('core:cart')


@require_POST
def cart_update(request):
    from paiement.cart_checkout import sync_paid_cart
    sync_paid_cart(request)
    cart = request.session.get('cart', {})
    key = request.POST.get('key', '')
    try:
        quantity = int(request.POST.get('quantity', '0'))
        if not 0 <= quantity <= 10:
            raise ValueError
    except (ValueError, TypeError):
        messages.error(request, _('La quantité doit être comprise entre 0 et 10.'))
        return redirect('core:cart')
    if key in cart:
        if quantity == cart[key]['quantity']:
            return redirect('core:cart')
        if quantity != cart[key]['quantity'] and not prepare_cart_change(request):
            return redirect('core:cart')
        cart = request.session.get('cart', {})
        if key not in cart:
            return redirect('core:cart')
        if quantity == 0:
            del cart[key]
        else:
            if cart[key].get('variant'):
                from stocks.models import Variant
                variant = Variant.objects.filter(pk=cart[key]['variant'],product_id=cart[key]['product']).first()
                if not variant or variant.available < quantity:
                    messages.error(request, _('La quantité demandée dépasse le stock disponible.'))
                    return redirect('core:cart')
            cart[key]['quantity'] = quantity
        request.session['cart'] = cart
    return redirect('core:cart')


def prepare_cart_change(request):
    import stripe
    from paiement.cart_checkout import sync_paid_cart, release_for_cart_change
    from stocks.reservations import StockUnavailable
    sync_paid_cart(request)
    try:
        return release_for_cart_change(request) is None
    except StockUnavailable as error:
        messages.error(request, str(error))
    except stripe.StripeError:
        messages.error(request, 'Le paiement ne peut pas encore être vérifié. Réessayez dans quelques instants ; le panier et le stock réservé sont conservés.')
    return False
