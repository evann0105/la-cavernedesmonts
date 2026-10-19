from django.shortcuts import render
from core.models import Product
from core.homepage import world_cards
from core.pricing import with_catalog_prices


def home(request):
    products = with_catalog_prices(Product.objects.filter(is_published=True).select_related('category')).order_by('-is_featured', 'id')[:4]
    return render(request, 'accueil/home.html', {'world_cards': world_cards(), 'products': products})
