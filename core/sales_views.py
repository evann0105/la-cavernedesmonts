"""A separate Soldes workspace for grouped and individual price operations."""
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .catalog_access import catalog_required
from .models import Category, ProductPromotion, SaleCampaign
from .pricing import load_pricing_context, price_quote, regular_price
from .promotion_views import _promotion_rows
from .sales_forms import SaleCampaignForm, SaleSelectionForm
from .sales_services import (cancel_campaign, cancellation_token, create_campaign,
    matching_rows, read_preview, reference_targets, selection_from_snapshot, sign_preview)


def _filters(data):
    normalized = data.copy()
    for name, default in [('stock', 'all'), ('publication', 'published')]:
        if name not in normalized:
            normalized[name] = default
    return SaleSelectionForm(normalized)


def _filter_hidden(criteria):
    return [{'name': key, 'value': value} for key, values in criteria.items()
        for value in (values if isinstance(values, list) else [values]) if value != '']


def _selection_url(criteria):
    return reverse('core:sales_create') + '?' + urlencode(criteria, doseq=True)


def _campaign_row(campaign, promotions, now, user):
    children = _promotion_rows(promotions, now)
    statuses = {row['status'] for row in children}
    status = next((state for state in ['active', 'scheduled', 'ended', 'cancelled'] if state in statuses), 'cancelled')
    labels = {'active': 'En cours', 'scheduled': 'Programmée', 'ended': 'Terminée', 'cancelled': 'Annulée'}
    live = [row for row in children if row['can_cancel']]
    return {'campaign': campaign, 'title': campaign.title, 'status': status, 'status_label': labels[status],
        'description': children[0]['description'] if children else '',
        'product_count': len({promo.product_id for promo in promotions}),
        'live_product_count': len({row['promotion'].product_id for row in live}),
        'cancelled_product_count': len([row for row in children if row['status'] == 'cancelled']),
        'target_count': sum(max(len(p.reference_prices), len(p.targets.all()), 1) for p in promotions),
        'starts_at': min((p.starts_at for p in promotions), default=None),
        'ends_at': max((p.ends_at for p in promotions), default=None), 'can_cancel': bool(live),
        'cancel_token': cancellation_token(campaign, promotions, user),
        'detail_url': reverse('core:sales_detail', args=[campaign.pk]),
        'cancel_url': reverse('core:sales_cancel', args=[campaign.pk])}


@catalog_required
@require_http_methods(['GET'])
def sales_dashboard(request):
    now = timezone.now()
    promotions = list(ProductPromotion.objects.select_related('product', 'campaign').prefetch_related('targets', 'product__stock_variants').order_by('-starts_at', '-pk'))
    groups = {}
    legacy = []
    for promotion in promotions:
        if promotion.campaign_id:
            groups.setdefault(promotion.campaign_id, {'campaign': promotion.campaign, 'promotions': []})['promotions'].append(promotion)
        else:
            legacy.append(promotion)
    campaign_rows = [_campaign_row(group['campaign'], group['promotions'], now, request.user) for group in groups.values()]
    standalone_rows = _promotion_rows(legacy, now)
    for row in standalone_rows:
        promotion = row['promotion']
        row.update({'product': promotion.product, 'title': promotion.product.name, 'product_count': 1,
            'target_count': max(len(promotion.reference_prices), len(promotion.targets.all()), 1), 'starts_at': promotion.starts_at, 'ends_at': promotion.ends_at,
            'detail_url': reverse('core:catalog_promotions', args=[promotion.product_id]),
            'cancel_url': reverse('core:catalog_promotion_cancel', args=[promotion.product_id, promotion.pk])})
    all_rows = campaign_rows + standalone_rows
    counts = {state: sum(row['status'] == state for row in all_rows) for state in ['active', 'scheduled', 'ended', 'cancelled']}
    counts['all'] = len(all_rows)
    status = request.GET.get('status', 'all')
    if status not in counts:
        status = 'all'
    # Product cards show the exact reductions currently active, irrespective of tabs.
    active_product_rows = []
    products = {promotion.product_id: promotion.product for promotion in promotions}
    load_pricing_context(list(products.values()), at=now)
    for row in _promotion_rows(promotions, now):
        promotion = row['promotion']
        product = products[promotion.product_id]
        if row['status'] == 'active':
            targets = list(product.stock_variants.all()) or [None] if promotion.all_variants else list(promotion.targets.all())
            quotes = [price_quote(product, variant, at=now) for variant in targets]
            effective = [quote for quote in quotes if quote.has_discount and quote.promotion_id == promotion.pk]
            if not effective:
                continue
            active_product_rows.append({**row, 'product': product, 'target_count': len(effective),
                'detail_url': reverse('core:sales_detail', args=[promotion.campaign_id]) if promotion.campaign_id else reverse('core:catalog_promotions', args=[promotion.product_id])})
    return render(request, 'core/sales/dashboard.html', {'status': status, 'counts': counts,
        'campaign_rows': [row for row in campaign_rows if status == 'all' or row['status'] == status],
        'standalone_rows': [row for row in standalone_rows if status == 'all' or row['status'] == status],
        'active_product_rows': active_product_rows, 'active_product_count': len({row['product'].pk for row in active_product_rows})})


def _selection_response(request, filter_form, error=None):
    criteria = filter_form.criteria() if filter_form.is_valid() else None
    rows = matching_rows(criteria) if criteria is not None else []
    if error:
        filter_form.add_error(None, error)
    return render(request, 'core/sales/create.html', {'stage': 'selection', 'filter_form': filter_form,
        'candidate_rows': rows, 'selected_product_ids': [row['product'].pk for row in rows],
        'filter_hidden': _filter_hidden(criteria) if criteria is not None else []})


@catalog_required
@require_http_methods(['GET', 'POST'])
def sales_create(request):
    if request.method == 'GET':
        return _selection_response(request, _filters(request.GET))
    action = request.POST.get('action')
    if action == 'preview':
        filter_form = _filters(request.POST)
        if not filter_form.is_valid():
            return _selection_response(request, filter_form)
        criteria = filter_form.criteria()
        candidates = matching_rows(criteria)
        raw_ids = request.POST.getlist('product_ids')
        try:
            selected = {int(value) for value in raw_ids}
        except (TypeError, ValueError):
            selected = set()
        eligible = {row['product'].pk for row in candidates}
        if not selected or not selected <= eligible or len(raw_ids) != len(selected):
            return _selection_response(request, filter_form, 'Sélectionnez au moins un article parmi les résultats de vos critères.')
        rows = [row for row in candidates if row['product'].pk in selected]
        token = sign_preview(rows, criteria, request.user)
        form = SaleCampaignForm(targets=reference_targets(rows), initial={'kind': 'percent', 'value': 10})
    elif action == 'save':
        try:
            token = request.POST.get('preview_token', '')
            snapshot = read_preview(token, request.user)
            criteria = snapshot['criteria']
            rows = selection_from_snapshot(snapshot)
        except ValidationError as error:
            return _selection_response(request, _filters(QueryDict()), error.messages[0])
        form = SaleCampaignForm(request.POST, targets=reference_targets(rows))
        if form.is_valid():
            try:
                campaign = create_campaign(form, snapshot, request.user)
            except ValidationError as error:
                return _selection_response(request, _filters(QueryDict(urlencode(criteria, doseq=True))), error.messages[0])
            if campaign is not None:
                LogEntry.objects.log_actions(user_id=request.user.pk, queryset=SaleCampaign.objects.filter(pk=campaign.pk),
                    action_flag=ADDITION, change_message=f'Opération créée après aperçu confirmé : {len(rows)} produits, critères {criteria}.')
                LogEntry.objects.log_actions(user_id=request.user.pk, queryset=ProductPromotion.objects.filter(campaign=campaign),
                    action_flag=ADDITION, change_message=f'Réduction programmée dans l’opération « {campaign.title} ». Prix de référence confirmés et sélection figée.')
                messages.success(request, 'L’opération est enregistrée. Les prix changeront automatiquement aux dates prévues sur les articles sélectionnés.')
                return redirect('core:sales_detail', pk=campaign.pk)
    else:
        return _selection_response(request, _filters(QueryDict()), 'Recommencez la sélection avant de valider.')
    return render(request, 'core/sales/create.html', {'stage': 'preview', 'form': form,
        'target_rows': form.target_rows, 'preview_token': token,
        'preview_summary': {'product_count': len(rows), 'target_count': len(form.target_rows)},
        'selection_url': _selection_url(criteria), 'filter_hidden': _filter_hidden(criteria)})


@catalog_required
@require_http_methods(['GET'])
def sales_detail(request, pk):
    campaign = get_object_or_404(SaleCampaign, pk=pk)
    now = timezone.now()
    promotions = list(campaign.promotions.select_related('product').prefetch_related('targets', 'product__stock_variants').order_by('product__name', 'pk'))
    products = {promotion.product_id: promotion.product for promotion in promotions}
    load_pricing_context(list(products.values()), at=now)
    for promotion in promotions:
        promotion.product = products[promotion.product_id]
    promotion_rows = _promotion_rows(promotions, now)
    for row in promotion_rows:
        promotion = row['promotion']
        product = promotion.product
        targets = list(promotion.targets.all()) or [None]
        refs = []
        for variant in targets:
            key = str(variant.pk) if variant else 'product'
            refs.append({'label': f'{variant.size} · {variant.color}' if variant else 'Prix du produit',
                'regular_price': regular_price(product, variant), 'reference': promotion.reference_prices.get(key),
                'current': price_quote(product, variant, at=now).current})
        row.update({'product': product, 'reference_rows': refs,
            'detail_url': reverse('core:catalog_promotions', args=[product.pk])})
    row = _campaign_row(campaign, promotions, now, request.user)
    criteria_labels = []
    criteria = campaign.criteria
    if criteria.get('categories'):
        criteria_labels.append('Catégories : ' + ', '.join(Category.objects.filter(pk__in=criteria['categories']).values_list('name', flat=True)))
    labels = {'q': 'Recherche', 'article_type': 'Type', 'price_min': 'Prix minimum', 'price_max': 'Prix maximum', 'color': 'Couleur', 'size': 'Taille'}
    for name, label in labels.items():
        if criteria.get(name):
            criteria_labels.append(f'{label} : {criteria[name]}')
    if criteria.get('stock') in ('available', 'zero'):
        criteria_labels.append('Stock : ' + ('disponible' if criteria['stock'] == 'available' else 'en rupture'))
    return render(request, 'core/sales/detail.html', {'campaign': campaign, 'row': row,
        'promotion_rows': promotion_rows, 'criteria_labels': criteria_labels})


@catalog_required
@require_POST
def sales_cancel(request, pk):
    get_object_or_404(SaleCampaign, pk=pk)
    try:
        campaign, changed = cancel_campaign(pk, request.POST.get('revision'), request.POST.get('cancel_token', ''), request.user)
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        if changed:
            LogEntry.objects.log_actions(user_id=request.user.pk, queryset=ProductPromotion.objects.filter(pk__in=[p.pk for p in changed]),
                action_flag=CHANGE, change_message=f'Annulation de l’opération « {campaign.title} ». Historique conservé.')
            LogEntry.objects.log_actions(user_id=request.user.pk, queryset=SaleCampaign.objects.filter(pk=pk),
                action_flag=CHANGE, change_message='Opération annulée. Les périodes et les prix de référence sont conservés.')
            messages.success(request, 'L’opération est annulée. Les prix habituels s’appliquent à nouveau.')
        else:
            messages.info(request, 'Cette opération est déjà terminée ou annulée.')
    return redirect('core:sales_detail', pk=pk)
