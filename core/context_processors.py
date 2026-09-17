def basket(request):
    return {'cart_count': sum(i.get('quantity', 0) for i in request.session.get('cart', {}).values())}


def catalog_access(request):
    from .catalog_access import can_manage_catalog
    return {'can_manage_catalog': can_manage_catalog(request.user)}


def back_navigation(request):
    from urllib.parse import urlsplit
    from django.urls import reverse
    from django.utils.http import url_has_allowed_host_and_scheme

    match = request.resolver_match
    name = match.view_name if match else ''
    if name == 'accueil:home':
        return {'back_url': None}
    fallback = reverse('accueil:home')
    if name.startswith('core:catalog_') and name != 'core:catalog_list':
        fallback = reverse('core:catalog_list')
    elif name == 'core:product_detail':
        fallback = reverse('core:product_list')
    elif name.startswith('paiement:'):
        fallback = reverse('core:cart')
    # A normal link also works without JavaScript and never replays a submitted form.
    referer = request.META.get('HTTP_REFERER', '')
    if url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        try:
            previous = urlsplit(referer)
            current = urlsplit(request.build_absolute_uri())
            target = previous.path + (('?' + previous.query) if previous.query else '')
            if (not previous.netloc or (previous.scheme, previous.netloc) == (current.scheme, current.netloc)) and target.startswith('/') and not target.startswith('//') and target != request.get_full_path():
                if not previous.path.startswith(('/accounts/', '/account/', '/admin/')):
                    return {'back_url': target + (('#' + previous.fragment) if previous.fragment else '')}
        except ValueError:
            pass
    return {'back_url': fallback}
