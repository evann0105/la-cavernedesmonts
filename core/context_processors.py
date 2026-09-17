def basket(request):
    return {'cart_count': sum(i.get('quantity', 0) for i in request.session.get('cart', {}).values())}


def catalog_access(request):
    from .catalog_access import can_manage_catalog
    return {'can_manage_catalog': can_manage_catalog(request.user)}
