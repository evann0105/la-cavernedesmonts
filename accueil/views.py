from django.shortcuts import render

from boutique.configuration import configuration as shop_configuration
from core.homepage import world_cards
from .configuration import featured_products, public_configuration


def home(request):
    shop = shop_configuration()
    content = public_configuration(shop=shop)
    # The metadata context processor reuses the same resolved home, including
    # the selected hero image, instead of loading its configuration again.
    request._homepage_configuration = content
    return render(request, 'accueil/home.html', {
        'home': content, 'shop': shop,
        'world_cards': world_cards(), 'products': featured_products(),
    })
