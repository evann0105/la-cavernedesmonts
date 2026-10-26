from collections import Counter
from datetime import datetime
from decimal import Decimal
from django.core.paginator import Paginator
from django.db.models import Count, Sum, Q
from django.db.models.functions import ExtractMonth, ExtractYear
from django.shortcuts import render
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_GET
from core.catalog_access import catalog_required
from paiement.models import Order
from stocks.models import Variant

MONTHS = ['Janvier','Février','Mars','Avril','Mai','Juin','Juillet','Août','Septembre','Octobre','Novembre','Décembre']


def bounds(year, month=None):
    start = timezone.make_aware(datetime(year, month or 1, 1))
    end = timezone.make_aware(datetime(year + 1, 1, 1) if not month or month == 12 else datetime(year, month + 1, 1))
    return start, end


def euros(cents):
    return Decimal(cents or 0) / 100


@catalog_required
@require_GET
def dashboard(request):
    from retours.access import manager
    from retours.worklist import dashboard_attention
    from clients.worklist import dashboard_attention as sav_dashboard_attention
    return_attention = dashboard_attention() if manager(request.user) else None
    sav_attention = sav_dashboard_attention() if manager(request.user) else None
    today = timezone.localdate()
    try:
        year = int(request.GET.get('annee', today.year))
        month = int(request.GET.get('mois', today.month))
        if not 2000 <= year <= 2100 or not 0 <= month <= 12:
            raise ValueError
    except (ValueError, TypeError):
        year, month = today.year, today.month
    start, end = bounds(year, month)
    # A recorded payment date and amount are required: never invent missing revenue.
    paid = Order.objects.filter(status='paid', paid_at__isnull=False, total_paid__isnull=False)
    selected = paid.filter(paid_at__gte=start, paid_at__lt=end)
    annual_start, annual_end = bounds(year)
    annual = paid.filter(paid_at__gte=annual_start, paid_at__lt=annual_end)
    from retours.models import ReturnRequest
    refunded=ReturnRequest.objects.filter(order__status='paid',refund_state='succeeded',refunded_at__gte=start,refunded_at__lt=end).aggregate(total=Sum('amount'))['total'] or 0
    stats = selected.aggregate(amount=Sum('total_paid'), count=Count('pk'))
    amount, count = stats['amount'] or 0, stats['count']
    monthly = {row['month']:row for row in annual.annotate(month=ExtractMonth('paid_at')).values('month').annotate(amount=Sum('total_paid'), count=Count('pk'))}
    maximum = max([row['amount'] for row in monthly.values()] or [1]) or 1
    rows = [{'number':i, 'label':label, 'amount':euros(monthly.get(i,{}).get('amount')), 'cents':monthly.get(i,{}).get('amount',0), 'count':monthly.get(i,{}).get('count',0)} for i,label in enumerate(MONTHS,1)]
    quantities = Counter(); names = {}; article_amounts = Counter()
    for items in selected.values_list('items',flat=True).iterator():
        for item in items:
            key = str(item.get('product', item.get('name','Article')))
            quantities[key] += item['quantity']
            names[key] = item['name']
            article_amounts[key] += item['quantity'] * item['unit_amount']
    top = [{'name':names[key], 'quantity':qty, 'amount':euros(article_amounts[key])} for key,qty in quantities.most_common(8)]
    years = sorted(set(paid.annotate(y=ExtractYear('paid_at')).values_list('y',flat=True)) | {today.year,year}, reverse=True)
    orders = Paginator(selected.order_by('-paid_at','-pk'),15).get_page(request.GET.get('page'))
    response = render(request,'tableau/dashboard.html',{
        'return_attention':return_attention, 'sav_attention':sav_attention,
        'year':year,'month':month,'years':years,'months':list(enumerate(MONTHS,1)),
        'period':f'{MONTHS[month-1]} {year}' if month else f'Année {year}',
        'refund_amount':euros(refunded),'net_amount':euros(amount-refunded),'amount':euros(amount),'order_count':count,'average':euros(amount)/count if count else Decimal(0),
        'units':sum(quantities.values()),'annual_amount':euros(annual.aggregate(amount=Sum('total_paid'))['amount']),
        'monthly':rows,'maximum':maximum,'top_products':top,'page_obj':orders,
        'pending':Order.objects.filter(status='pending',created_at__gte=start,created_at__lt=end).count(),
        'incomplete':Order.objects.filter(status='paid').filter(Q(paid_at__isnull=True) | Q(total_paid__isnull=True)).count(),
        'out_of_stock':Variant.objects.filter(quantity=0).count(),
        'unknown_stock':Variant.objects.filter(quantity__isnull=True).count(),
    })
    # This response now includes private SAV contacts as well as sales figures.
    patch_cache_control(response, private=True, no_store=True, max_age=0)
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response
