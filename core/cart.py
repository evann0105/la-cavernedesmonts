from decimal import Decimal
from .models import Product
from stocks.models import Variant


def lines(request):
    from paiement.cart_checkout import sync_paid_cart
    sync_paid_cart(request)
    result = []
    cart = request.session.get('cart', {})
    products = {str(p.pk): p for p in Product.objects.filter(is_published=True, pk__in=[v['product'] for v in cart.values()])}
    variants = {v.pk:v for v in Variant.objects.filter(pk__in=[v.get('variant') for v in cart.values() if v.get('variant')])}
    for key, item in cart.items():
        product = products.get(str(item['product']))
        if product:
            quantity = item['quantity']
            variant = variants.get(item.get('variant'))
            if variant and (variant.product_id != product.pk or
                            (variant.size, variant.color) != (item.get('size'), item.get('color', variant.color))):
                variant = None
            result.append({'key':key,'product':product,'variant':variant,'size':variant.size if variant else item['size'],'color':variant.color if variant else item.get('color', ''), 'quantity':quantity,'subtotal':product.price*quantity,'stock_ok':bool(variant and variant.available >= quantity)})
    return result


def total(items):
    return sum((i['subtotal'] for i in items), Decimal('0.00'))
