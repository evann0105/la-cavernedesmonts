from django.contrib import messages
from django.contrib.admin.models import LogEntry, ADDITION, CHANGE
from django.contrib.contenttypes.models import ContentType
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum, Q, F
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from core.catalog_access import catalog_required
from core.models import Product
from .models import Variant
from .forms import VariantForm, AdjustmentForm
from .adjustments import adjust_stock, AdjustmentError
from .identity import identity_change_error, identity_lock_reason, record_label_correction


def stock_history(product):
    ids = [str(pk) for pk in product.stock_variants.values_list('pk', flat=True)]
    return LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(Variant),
        object_id__in=ids).select_related('user').order_by('-action_time', '-pk')[:15]


@catalog_required
@require_http_methods(['GET'])
def overview(request):
    query = request.GET.get('q', '').strip()[:100]
    products = Product.objects.annotate(variant_count=Count('stock_variants'), total_stock=Sum('stock_variants__quantity'),
        total_reserved=Sum('stock_variants__reserved'), total_available=Sum(F('stock_variants__quantity')-F('stock_variants__reserved')),
        unknown_count=Count('stock_variants', filter=Q(stock_variants__quantity__isnull=True))).order_by('name', 'pk')
    totals = Variant.objects.aggregate(physical_units=Sum('quantity'), reserved_units=Sum('reserved'),
        available_units=Sum(F('quantity')-F('reserved')))
    summary = {'products':products.count(), 'physical':totals['physical_units'] or 0,
        'reserved':totals['reserved_units'] or 0, 'available':totals['available_units'] or 0,
        'to_count':products.filter(Q(variant_count=0) | Q(unknown_count__gt=0)).count()}
    if query:
        products = products.filter(name__icontains=query)
    return render(request, 'stocks/overview.html', {'page_obj':Paginator(products, 20).get_page(request.GET.get('page')), 'query':query, 'summary':summary})


@catalog_required
@require_http_methods(['GET', 'POST'])
def product_stock(request, pk, variant_pk=None):
    product = get_object_or_404(Product, pk=pk)
    variant = get_object_or_404(Variant, pk=variant_pk, product=product) if variant_pk else Variant(product=product)
    form = VariantForm(request.POST if request.method == 'POST' else None, instance=variant)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                # Serialize edits and additions for this product.
                Product.objects.select_for_update().get(pk=product.pk)
                old = Variant.objects.select_for_update().get(pk=variant_pk, product=product) if variant_pk else None
                if old and old.revision != form.cleaned_data['revision']:
                    form.add_error(None, 'Le stock a été modifié dans un autre onglet. Rechargez la page avant de corriger la quantité.')
                elif old and (identity_error := identity_change_error(old, form.cleaned_data['size'], form.cleaned_data['color'])):
                    form.add_error(None, identity_error)
                elif old and old.reserved and (form.cleaned_data['quantity'] is None or form.cleaned_data['quantity'] < old.reserved or form.cleaned_data['size'] != old.size or form.cleaned_data['color'] != old.color):
                    form.add_error(None, 'Des articles sont réservés : conservez cette taille/couleur et une quantité au moins égale au stock réservé.')
                else:
                    saved = form.save(commit=False)
                    saved.reserved = old.reserved if old else 0
                    saved.revision = (old.revision + 1) if old else 1
                    saved.save()
                    record_label_correction(old, saved)
                    before = old.quantity if old else 'nouvelle variante'
                    labels = (f'Taille : {old.size} → {saved.size}. Couleur : {old.color} → {saved.color}.'
                        if old else f'Taille : {saved.size}. Couleur : {saved.color}.')
                    LogEntry.objects.log_actions(user_id=request.user.pk, queryset=Variant.objects.filter(pk=saved.pk), action_flag=CHANGE if old else ADDITION, change_message=f'Stock : {before} → {saved.quantity}. {labels}')
                    messages.success(request, 'Variante et stock enregistrés.')
                    return redirect('stocks:product', pk=product.pk)
        except IntegrityError:
            form.add_error(None, 'Cette combinaison existe déjà. Rechargez la page pour retrouver les variantes enregistrées.')
    return render(request, 'stocks/product.html', {'product':product, 'variants':product.stock_variants.all(), 'form':form,
        'editing':bool(variant_pk), 'history':stock_history(product),
        'identity_lock_reason':identity_lock_reason(variant) if variant_pk else ''})


@catalog_required
@require_http_methods(['GET', 'POST'])
def adjustment(request, pk, variant_pk):
    product = get_object_or_404(Product, pk=pk)
    variant = get_object_or_404(Variant, pk=variant_pk, product=product)
    form = AdjustmentForm(request.POST if request.method == 'POST' else None, variant=variant)
    if request.method == 'POST' and form.is_valid():
        try:
            saved, before = adjust_stock(product.pk, variant.pk, actor=request.user,
                operation=form.cleaned_data['operation'], amount=form.cleaned_data['amount'],
                revision=form.cleaned_data['revision'], reason=form.cleaned_data['reason'])
        except AdjustmentError as error:
            form.add_error(None, str(error))
            variant.refresh_from_db()
        except (Product.DoesNotExist, Variant.DoesNotExist):
            raise Http404
        else:
            if saved.quantity == before:
                messages.info(request, 'La quantité est déjà à jour. Aucun mouvement ajouté.')
            else:
                previous = 'à compter' if before is None else str(before)
                messages.success(request, f'Stock enregistré : {previous} → {saved.quantity} unité(s), dont {saved.available} disponible(s).')
            return redirect('stocks:product', pk=product.pk)
    return render(request, 'stocks/adjust.html', {'product':product, 'variant':variant, 'form':form})
