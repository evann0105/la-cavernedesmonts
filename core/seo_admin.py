"""Read-only product search preparation; no ranking score or invented facts."""
from collections import Counter
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_GET
from .catalog_access import catalog_required
from .models import Product
from .seo import indexing_allowed, site_origin
from .seo_products import build_product_seo


@catalog_required
@require_GET
def overview(request):
    products = list(Product.objects.filter(duplicate_of__isnull=True).select_related('category').prefetch_related('stock_variants').order_by('name', 'pk'))
    rows = [{'product': product, 'metadata': build_product_seo(product), 'brand_review': product.information_sources.get('_brand_review', {})} for product in products]
    titles = Counter(row['metadata'].title.casefold() for row in rows)
    for row in rows:
        row['duplicate_title'] = titles[row['metadata'].title.casefold()] > 1
    query = request.GET.get('q', '').strip()[:100]
    needs_review = request.GET.get('a_verifier') == '1'
    filtered = [row for row in rows if (not query or query.casefold() in (row['product'].name + ' ' + row['product'].brand).casefold())
                and (not needs_review or row['metadata'].warnings or row['duplicate_title'])]
    page = Paginator(filtered, 20).get_page(request.GET.get('page'))
    return render(request, 'core/seo/overview.html', {
        'page_obj': page, 'query': query, 'needs_review': needs_review,
        'total': len(rows), 'published': sum(product.is_published for product in products),
        'prepared': sum(bool(row['metadata'].title and row['metadata'].description and row['metadata'].keywords) for row in rows),
        'to_review': sum(bool(row['metadata'].warnings or row['duplicate_title']) for row in rows),
        'archived_duplicates': Product.objects.filter(duplicate_of__isnull=False).count(),
        'indexing_enabled': indexing_allowed(), 'site_origin': site_origin(),
    })
