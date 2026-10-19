from django.contrib import messages
from django.contrib.admin.models import LogEntry, ADDITION, CHANGE
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Max
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from .catalog_access import catalog_required
from .catalog_forms import CatalogProductForm, GalleryFormSet, ColorPhotoForms, OriginalColorForm
from .models import Category, Product, ProductImage, ProductColor
from .views import product_detail
from stocks.forms import InitialStockForms, VariantPriceForms
from stocks.models import Variant
from .pricing import with_catalog_prices


@catalog_required
@require_http_methods(['GET'])
def catalog_list(request):
    products = with_catalog_prices(Product.objects.select_related('category')).order_by('-created_at', '-pk')
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
@transaction.atomic
def catalog_edit(request, pk=None):
    products = Product.objects.select_for_update() if request.method == 'POST' else Product.objects.all()
    product = get_object_or_404(products, pk=pk) if pk else Product(is_published=False)
    previous_product_price = product.price
    form = CatalogProductForm(request.POST if request.method == 'POST' else None, request.FILES or None, instance=product)
    photos = GalleryFormSet(request.POST if request.method == 'POST' else None, request.FILES or None, instance=product, prefix='photos')
    initial_stock = InitialStockForms(request.POST if request.method == 'POST' else None, prefix='stock', product=product)
    colors = ColorPhotoForms(request.POST if request.method == 'POST' else None, request.FILES or None, instance=product, prefix='colors')
    original_color_name = product.main_color
    original_data = request.POST if request.method == 'POST' and any(key.startswith('original-') for key in request.POST) else None
    original_color_form = OriginalColorForm(original_data, prefix='original', instance=ProductColor(product=product, name=original_color_name))
    original_color_form.initial['name'] = original_color_name
    original_color_form.original_image_url = product.image_url
    existing_stock = {}
    stock_variants = list(product.stock_variants.select_for_update() if request.method == 'POST' and product.pk else product.stock_variants.all()) if product.pk else []
    price_data = request.POST if request.method == 'POST' and any(key.startswith('variant_prices-') for key in request.POST) else None
    variant_prices = VariantPriceForms(price_data, product=product, variants=stock_variants, prefix='variant_prices',
        initial=[{'variant_id': variant.pk, 'revision': variant.revision, 'price': variant.price} for variant in stock_variants])
    if product.pk:
        for variant in stock_variants:
            key = ' '.join(variant.color.split()).casefold()
            existing_stock.setdefault(key, []).append(variant)
    for color_form in colors.forms:
        # Keep the original stock identity visible when a photo name is edited.
        key = ' '.join(color_form.instance.name.split()).casefold()
        color_form.existing_stock = existing_stock.get(key, []) if color_form.instance.pk else []
        if color_form.existing_stock:
            color_form.fields['sizes'].label = 'Tailles à ajouter pour cette couleur'
    original_color_form.existing_stock = existing_stock.get(' '.join(original_color_name.split()).casefold(), []) if original_color_name else []
    if original_color_form.existing_stock:
        original_color_form.fields['sizes'].label = 'Tailles à ajouter pour cette couleur'
    assigned_prices = set()
    for color_form in [original_color_form] + list(colors.forms):
        color_form.price_forms = []
        for variant in color_form.existing_stock:
            matching = next((price_form for price_form in variant_prices.forms if price_form.variant and price_form.variant.pk == variant.pk), None)
            if matching is not None and variant.pk not in assigned_prices:
                color_form.price_forms.append(matching)
                assigned_prices.add(variant.pk)
    unlinked_price_forms = [price_form for price_form in variant_prices.forms if price_form.variant is None or price_form.variant.pk not in assigned_prices]
    if request.method == 'POST':
        valid_prices = variant_prices.is_valid() if variant_prices.is_bound else True
        valid_original = original_color_form.is_valid() if original_color_form.is_bound else True
        valid_colors = colors.is_valid()
        valid_stock = initial_stock.is_valid() if initial_stock is not None else True
        if valid_colors and valid_stock and valid_original:
            combinations = {
                (' '.join(size.split()).casefold(), ' '.join(color.split()).casefold())
                for size, color in product.stock_variants.values_list('size', 'color')
            } if product.pk else set()
            combinations.update(
                (stock_form.cleaned_data['size'].casefold(), stock_form.cleaned_data['color'].casefold())
                for stock_form in initial_stock.forms
                if stock_form.has_changed() and not stock_form.cleaned_data.get('DELETE')
            )
            color_forms = list(colors.forms) + ([original_color_form] if original_color_form.is_bound else [])
            for color_form in color_forms:
                if not color_form.cleaned_data or color_form.cleaned_data.get('DELETE'):
                    continue
                color_key = color_form.cleaned_data['name'].casefold()
                missing = [size for size in color_form.size_options if (size.casefold(), color_key) not in combinations]
                if missing:
                    color_form.add_error('sizes', 'Préparez les tailles et renseignez leur quantité avant d’enregistrer : ' + ', '.join(missing) + '.')
                    valid_colors = False
        color_prices = {}
        if valid_colors and valid_original:
            for color_form in list(colors.forms) + ([original_color_form] if original_color_form.is_bound else []):
                values = color_form.cleaned_data
                if not values or values.get('DELETE') or not values.get('name') or values.get('color_price') is None:
                    continue
                key = values['name'].casefold()
                if key in color_prices and color_prices[key] != values['color_price']:
                    color_form.add_error('color_price', 'Cette couleur possède déjà un autre prix commun. Utilisez un seul prix ou renseignez chaque taille.')
                    valid_colors = False
                else:
                    color_prices[key] = values['color_price']
        valid_form, valid_photos = form.is_valid(), photos.is_valid()
        if valid_form and valid_photos:
            remaining = sum(1 for photo in photos.forms if not photo.cleaned_data.get('DELETE'))
            if remaining + len(form.cleaned_data['new_photos']) > 50:
                form.add_error('new_photos', 'La galerie peut contenir au maximum 50 photos.')
                valid_form = False
        if valid_form and valid_photos and valid_stock and valid_colors and valid_original and valid_prices:
            with transaction.atomic():
                if original_color_form.is_bound:
                    form.instance.main_color = original_color_form.cleaned_data['name']
                product = form.save()
                if variant_prices.is_bound:
                    for price_form in variant_prices.forms:
                        if not price_form.cleaned_data:
                            continue
                        variant = price_form.variant
                        price = price_form.cleaned_data['price']
                        if variant.price == price:
                            continue
                        before = variant.price
                        variant.price = price
                        variant.revision += 1
                        variant.save(update_fields=['price', 'revision', 'updated_at'])
                        previous_label = f'{before:.2f} €' if before is not None else f'prix général du produit ({previous_product_price:.2f} €)'
                        next_label = f'{price:.2f} €' if price is not None else f'prix général du produit ({product.price:.2f} €)'
                        LogEntry.objects.log_actions(user_id=request.user.pk, queryset=Variant.objects.filter(pk=variant.pk), action_flag=CHANGE, change_message=f'Prix : {previous_label} → {next_label}. Taille : {variant.size}. Couleur : {variant.color}.')
                if initial_stock is not None:
                    for stock_form in initial_stock.forms:
                        if not stock_form.has_changed() or stock_form.cleaned_data.get('DELETE'):
                            continue
                        values = {key: stock_form.cleaned_data[key] for key in ('size', 'color', 'reference', 'quantity', 'price')}
                        if values['price'] is None:
                            values['price'] = color_prices.get(values['color'].casefold())
                        variant = Variant.objects.create(product=product, revision=1, **values)
                        LogEntry.objects.log_actions(user_id=request.user.pk, queryset=Variant.objects.filter(pk=variant.pk), action_flag=ADDITION, change_message=f'Stock initial : {variant.quantity}. Taille : {variant.size}. Couleur : {variant.color}. Prix : {variant.effective_price:.2f} €.')
                colors.instance = product
                colors.save()
                photos.instance = product
                photos.save()
                order = product.images.aggregate(last=Max('order'))['last'] or 0
                for index, image in enumerate(form.cleaned_data['new_photos'], start=1):
                    ProductImage.objects.create(product=product, image=image, alt=product.name, order=order+index)
                LogEntry.objects.log_actions(user_id=request.user.pk, queryset=Product.objects.filter(pk=product.pk), action_flag=CHANGE if pk else ADDITION, change_message='Produit enregistré depuis la gestion de la boutique.')
            messages.success(request, 'Produit enregistré. Il est visible dans la boutique.' if product.is_published else 'Produit enregistré en brouillon. Il reste invisible aux clients.')
            if product.is_published:
                return redirect('core:product_detail', slug=product.slug)
            return redirect('core:catalog_preview', pk=product.pk)
        messages.error(request, 'Vérifiez les champs signalés. Si vous aviez choisi des fichiers, sélectionnez-les à nouveau avant d’enregistrer.')
    return render(request, 'core/catalog/edit.html', {'form':form, 'photos':photos, 'product':product, 'initial_stock':initial_stock, 'colors':colors, 'original_color_form':original_color_form, 'original_color_name':original_color_name, 'variant_prices':variant_prices, 'unlinked_price_forms':unlinked_price_forms})


@catalog_required
@require_http_methods(['GET'])
def catalog_preview(request, pk):
    product = get_object_or_404(Product, pk=pk)
    return product_detail(request, product.slug, catalog_preview=True)


@catalog_required
@require_http_methods(['GET', 'POST'])
def catalog_homepage(request):
    from .catalog_forms import HomepageSelectionForm
    from .homepage import WORLD_CARDS, world_cards
    from .models import HomepageSelection
    initial = dict(HomepageSelection.objects.values_list('slot', 'product_id'))
    for card in world_cards():
        if card['slot'] not in initial and card['product']:
            initial[card['slot']] = card['product'].pk
    form = HomepageSelectionForm(request.POST if request.method == 'POST' else None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            for spec in WORLD_CARDS:
                HomepageSelection.objects.update_or_create(slot=spec['slot'], defaults={'product':form.cleaned_data[spec['slot']]})
            LogEntry.objects.log_actions(user_id=request.user.pk, queryset=HomepageSelection.objects.filter(slot__in=[s['slot'] for s in WORLD_CARDS]), action_flag=CHANGE, change_message='Sélection des cartes de l’accueil modifiée.')
        messages.success(request, 'Les trois cartes de « Votre envie du moment » ont été mises à jour.')
        return redirect('core:catalog_homepage')
    cards = world_cards()
    choices = {}
    for spec in WORLD_CARDS:
        products = list(form.fields[spec['slot']].queryset)
        choices[spec['slot']] = {str(p.pk): {'image':p.image_url, 'name':p.name} for p in products}
        choices[spec['slot']][''] = {'image':products[0].image_url, 'name':products[0].name} if products else {'image':'', 'name':'Aucun produit publié avec photo'}
    panels = [{'card':card, 'field':form[card['slot']]} for card in cards]
    return render(request, 'core/catalog/homepage.html', {'form':form, 'panels':panels, 'choices':choices})
