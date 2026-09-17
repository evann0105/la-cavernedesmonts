from decimal import Decimal
from .models import Product


def lines(request):
    result = []
    cart = request.session.get('cart', {})
    products = {str(p.pk): p for p in Product.objects.filter(is_published=True, pk__in=[v['product'] for v in cart.values()])}
    for key, item in cart.items():
        product = products.get(str(item['product']))
        if product:
            quantity = item['quantity']
            result.append({'key': key, 'product': product, 'size': item['size'], 'quantity': quantity, 'subtotal': product.price * quantity})
    return result


def total(items):
    return sum((i['subtotal'] for i in items), Decimal('0.00'))
