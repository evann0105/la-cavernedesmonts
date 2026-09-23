from django.shortcuts import render
from core.models import Product
from core.homepage import world_cards


def home(request):
    return render(request, 'accueil/home.html', {'world_cards': world_cards(), 'products': Product.objects.filter(is_published=True).select_related('category').order_by('-is_featured', 'id')[:4]})
