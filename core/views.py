from django.utils.translation import gettext as _, get_language
from pathlib import Path
from django.conf import settings
from django.core.paginator import Paginator
from django.urls import reverse
from .product_images import thumbnail_url
from .catalog_filters import with_article_type
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import render, get_object_or_404, redirect
from django.templatetags.static import static
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from django.http import JsonResponse
from django.utils import timezone
from .models import Product, Category
from .cart import lines, total
from .pricing import product_price_summary, with_catalog_prices


def product_list(request):
    products = Product.objects.filter(is_published=True).select_related('category').order_by('-is_featured', 'name', 'pk')
    products = with_catalog_prices(with_article_type(products))
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
        products = products.order_by('_catalog_min_price' if sort == 'prix' else '-_catalog_min_price', 'pk')
    selected_category = Category.objects.filter(slug=category).first() if category else None
    page = Paginator(products, 24).get_page(request.GET.get('page'))
    request._seo_page_number = page.number
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'core/product_list.html', {'products': page, 'page_obj': page, 'pagination_query': params.urlencode(), 'categories': Category.objects.all(), 'selected': category, 'selected_category': selected_category, 'query': query, 'sort': sort, 'article_type': article_type, 'availability': availability, 'size': size, 'size_choices': size_choices, 'type_choices': Product._meta.get_field('article_type').choices, 'availability_choices': Product._meta.get_field('availability').choices, 'intent_title': intent_title, 'intent_intro': intent_intro})


def product_detail(request, slug, catalog_preview=False):
    products = Product.objects.all() if catalog_preview else Product.objects.filter(Q(is_published=True) | Q(duplicate_of__isnull=False))
    product = get_object_or_404(products.select_related('category').prefetch_related('images', 'color_photos', 'stock_variants'), slug=slug)
    if product.duplicate_of_id and not catalog_preview:
        canonical = get_object_or_404(Product, pk=product.duplicate_of_id, is_published=True, duplicate_of__isnull=True)
        return redirect('core:product_detail', slug=canonical.slug, permanent=True)
    images = [product.image_url] if product.image_url else []
    images += [i.image.url for i in product.images.all()]
    from .product_images import imported_gallery_urls
    images = imported_gallery_urls(product, images)
    color_photos = list(product.color_photos.all())
    photo_alts = {photo.image.url: photo.alt for photo in product.images.all()}
    for color in color_photos:
        if color.image.url not in images:
            images.append(color.image.url)
        photo_alts[color.image.url] = f'{product.localized_name} — {color.name}'
    variants = list(product.stock_variants.all())
    selected_variant = None
    if 'variante' in request.GET:
        value = request.GET.get('variante', '')
        selected_variant = next((variant for variant in variants if str(variant.pk) == value), None)
        if selected_variant is None or len(request.GET.getlist('variante')) != 1:
            return redirect('core:product_detail', slug=slug, permanent=True)
        request._seo_variant_valid = True
    if selected_variant:
        color_key = ' '.join(selected_variant.color.split()).casefold()
        matching_photo = next((color.image.url for color in color_photos if ' '.join(color.name.split()).casefold() == color_key), '')
        if not matching_photo and ' '.join(product.main_color.split()).casefold() == color_key:
            matching_photo = product.image_url
        if matching_photo in images:
            images.remove(matching_photo)
            images.insert(0, matching_photo)
    from .pricing import load_pricing_context, price_quote, PriceSummary
    pricing_at = timezone.now()
    load_pricing_context([product], at=pricing_at)
    quotes = []
    for variant in variants:
        variant.price_quote = price_quote(product, variant, at=pricing_at)
        quotes.append(variant.price_quote)
    if not quotes:
        quotes.append(price_quote(product, at=pricing_at))
    next_price_change = min((quote.next_boundary for quote in quotes if quote.next_boundary), default=None)
    from .product_options import build_product_options
    options = build_product_options(product, variants, color_photos)

    gallery = [{'url': url, 'thumbnail': thumbnail_url(url), 'alt': photo_alts.get(url) or product.localized_name} for url in images]
    from .seo_products import build_product_seo
    from .seo_schema import build_product_schema
    from .seo import page_seo, public_absolute_url
    from paiement.views import ready
    metadata = build_product_seo(product, variants=variants)
    canonical = public_absolute_url(reverse('core:product_detail', kwargs={'slug': product.slug}))
    schema = build_product_schema(product, url=canonical, images=gallery, variants=variants, at=pricing_at, can_sell=bool(ready()))
    breadcrumb_items = [('Accueil', public_absolute_url(reverse('accueil:home')))]
    if product.category_id:
        from urllib.parse import urlencode
        breadcrumb_items.append((product.category.name, public_absolute_url(reverse('core:product_list') + '?' + urlencode({'categorie': product.category.slug}))))
    breadcrumb_items.append((product.name, canonical))
    breadcrumb = {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': index, 'name': name, 'item': url}
        for index, (name, url) in enumerate(breadcrumb_items, 1) if url]}
    seo = page_seo(request, product=product, product_metadata={'title': metadata.title, 'description': metadata.description, 'image': gallery[0]['url'] if gallery else ''}, structured_data=[schema, breadcrumb])
    summary = PriceSummary(selected_variant.price_quote.current, selected_variant.price_quote.current) if selected_variant else product_price_summary(product, variants)
    related = with_catalog_prices(Product.objects.filter(category=product.category, is_published=True).select_related('category').exclude(pk=product.pk))[:4]
    return render(request, 'core/product_detail.html', {'product': product, 'images': images, 'gallery': gallery, 'related': related, 'catalog_preview': catalog_preview,
        'variants':variants, 'product_sizes':options['sizes'], 'product_colors':options['colors'], 'price_summary':summary, 'selected_variant':selected_variant, 'seo':seo,
        'pricing_at':pricing_at, 'next_price_change':next_price_change,
        'has_available_variant':any(variant.available > 0 for variant in variants),
        'has_unknown_stock':any(variant.quantity is None for variant in variants)})


@require_GET
def product_prices(request, slug):
    from .pricing import load_pricing_context, price_quote
    product = get_object_or_404(Product.objects.prefetch_related('stock_variants'), slug=slug, is_published=True)
    at = timezone.now()
    load_pricing_context([product], at=at)
    variants = list(product.stock_variants.all())
    quotes = [(variant.pk, price_quote(product, variant, at=at)) for variant in variants]
    if not quotes:
        quotes = [(None, price_quote(product, at=at))]
    next_change = min((quote.next_boundary for _, quote in quotes if quote.next_boundary), default=None)
    response = JsonResponse({
        'server_time':at.isoformat(), 'next_change':next_change.isoformat() if next_change else None,
        'variants':[
            {'id':identifier, 'price':str(quote.current),
             'reference':str(quote.reference) if quote.has_discount and quote.reference is not None else None}
            for identifier, quote in quotes
        ],
    })
    response['Cache-Control'] = 'no-store'
    return response


def product_preview(request, slug):
    product = get_object_or_404(Product, Q(is_published=True) | Q(duplicate_of__isnull=False), slug=slug)
    if product.duplicate_of_id:
        product = get_object_or_404(Product, pk=product.duplicate_of_id, is_published=True, duplicate_of__isnull=True)
    return redirect('core:product_detail', slug=product.slug, permanent=True)


def location_legacy(request):
    return redirect('core:location', permanent=True)


def location(request):
    return render(request, 'core/location.html')


@never_cache
@require_GET
def cart(request):
    from paiement.shipping import shipping_summary
    from paiement.benefits import SESSION_KEY, selection, summary_context
    from paiement.cart_checkout import current_order
    items = lines(request)
    subtotal = total(items)
    if not items:
        request.session.pop(SESSION_KEY, None)
        request.session.pop('checkout_displayed_benefit', None)
    benefit, quoted, error = selection(request, int(subtotal * 100), order=current_order(request))
    return render(request, 'core/cart.html', {
        'cart_items': items, 'total': subtotal, 'shipping': shipping_summary(subtotal),
        **summary_context(request, subtotal, benefit, quoted, error), 'code_return_to': 'cart',
    })


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
        if product.main_color or product.color_photos.exists():
            messages.error(request, _('Le stock de ce coloris est à confirmer avec la boutique.'))
            return redirect('core:product_detail', slug=slug)
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
