"""Private catalog management for scheduled, auditable reductions."""
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .catalog_access import catalog_required
from .models import Product, ProductPromotion
from .promotion_forms import ProductPromotionForm


def _promotion_rows(promotions, now):
    rows = []
    for promotion in promotions:
        if not promotion.enabled:
            status, label = 'cancelled', 'Annulée'
        elif promotion.ends_at <= now:
            status, label = 'ended', 'Terminée'
        elif promotion.starts_at > now:
            status, label = 'scheduled', 'Programmée'
        else:
            status, label = 'active', 'En cours'
        if promotion.kind == 'percent':
            description = f'−{promotion.value.normalize():f} %'
        elif promotion.kind == 'amount':
            description = f'−{promotion.value:.2f} €'
        else:
            description = f'Prix final : {promotion.value:.2f} €'
        targets = list(promotion.targets.all())
        scope = 'Toutes les tailles et couleurs' if promotion.all_variants else ', '.join(f'{target.size} · {target.color}' for target in targets)
        rows.append({'promotion': promotion, 'status': status, 'status_label': label,
            'description': description, 'scope': scope, 'can_cancel': promotion.enabled and promotion.ends_at > now})
    return rows


@catalog_required
@require_http_methods(['GET', 'POST'])
@transaction.atomic
def catalog_promotions(request, pk):
    products = Product.objects.select_for_update() if request.method == 'POST' else Product.objects.all()
    product = get_object_or_404(products, pk=pk)
    variants = product.stock_variants.select_for_update() if request.method == 'POST' else product.stock_variants.all()
    variants = list(variants.order_by('color', 'size', 'pk'))
    promotions = ProductPromotion.objects.filter(product=product).order_by('-starts_at', '-pk').prefetch_related('targets')
    if request.method == 'POST':
        promotions = promotions.select_for_update()
    promotions = list(promotions)
    now = timezone.now()
    form = ProductPromotionForm(request.POST if request.method == 'POST' else None,
        product=product, variants=variants, promotions=promotions, now=now)
    if request.method == 'POST' and form.is_valid():
        promotion = form.save()
        LogEntry.objects.log_actions(user_id=request.user.pk,
            queryset=ProductPromotion.objects.filter(pk=promotion.pk), action_flag=ADDITION,
            change_message=f'Réduction programmée : {promotion.get_kind_display()}, valeur {promotion.value}. Du {promotion.starts_at.isoformat()} au {promotion.ends_at.isoformat()}. Prix de référence confirmés : {promotion.reference_prices}.')
        messages.success(request, 'La réduction est enregistrée. Elle commencera et se terminera automatiquement aux dates prévues.')
        return redirect('core:catalog_promotions', pk=product.pk)
    return render(request, 'core/catalog/promotions.html', {'product': product, 'form': form,
        'promotion_rows': _promotion_rows(promotions, now), 'reference_rows': form.reference_rows})


@catalog_required
@require_POST
@transaction.atomic
def catalog_promotion_cancel(request, pk, promotion_pk):
    product = get_object_or_404(Product.objects.select_for_update(), pk=pk)
    promotion = get_object_or_404(ProductPromotion.objects.select_for_update(), pk=promotion_pk, product=product)
    try:
        revision = int(request.POST.get('revision', ''))
    except (ValueError, TypeError):
        revision = -1
    if revision != promotion.revision:
        messages.error(request, 'Cette réduction a changé depuis l’ouverture de la page. Vérifiez son état avant de recommencer.')
    elif not promotion.enabled or promotion.ends_at <= timezone.now():
        messages.info(request, 'Cette réduction est déjà annulée ou terminée.')
    else:
        promotion.enabled = False
        promotion.cancelled_at = timezone.now()
        promotion.revision += 1
        promotion.save(update_fields=['enabled', 'cancelled_at', 'revision', 'updated_at'])
        LogEntry.objects.log_actions(user_id=request.user.pk,
            queryset=ProductPromotion.objects.filter(pk=promotion.pk), action_flag=CHANGE,
            change_message='Réduction annulée. La période et les prix de référence sont conservés dans l’historique.')
        messages.success(request, 'La réduction est annulée. Le prix habituel s’applique à nouveau.')
    return redirect('core:catalog_promotions', pk=product.pk)
