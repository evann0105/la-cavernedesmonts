"""Read-only, frozen IDs for the explicit « all filtered results » action."""
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .catalog_access import catalog_required
from .catalog_bulk import MAX_PRODUCTS
from .catalog_query import catalog_queryset, normalize_catalog_filters


@catalog_required
@require_GET
def catalog_selection_results(request):
    products = catalog_queryset(normalize_catalog_filters(request.GET)).filter(duplicate_of__isnull=True)
    ids = list(products.values_list('pk', flat=True)[:MAX_PRODUCTS + 1])
    if len(ids) > MAX_PRODUCTS:
        return JsonResponse({'error': f'Cette recherche dépasse {MAX_PRODUCTS} articles. Précisez les filtres avant de tout sélectionner.'}, status=400)
    return JsonResponse({'ids': [str(pk) for pk in ids], 'count': len(ids)})
