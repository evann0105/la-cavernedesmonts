from django.shortcuts import render
from core.models import Product


def home(request):
    return render(request, 'accueil/home.html', {'products': Product.objects.order_by('-is_featured', 'id')[:4]})
