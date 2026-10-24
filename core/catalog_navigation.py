"""Keep the merchant's catalogue position without accepting arbitrary redirects."""
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

from django.urls import reverse

from .catalog_query import normalize_catalog_filters

_PRODUCT_ANCHOR = re.compile(r'catalog-product-[1-9][0-9]{0,18}\Z')


def safe_catalog_return(value):
    """Return only the catalogue URL with normalized filters, page and row anchor.

    The path must match literally. Even an absolute URL on our own host is not
    accepted: no host, scheme, relative path or URL-encoded path is needed here.
    Unknown query parameters never survive into a later redirect.
    """
    if not isinstance(value, str) or not value or len(value) > 2000 or value != value.strip():
        return ''
    if '\\' in value or any(ord(character) < 32 or ord(character) == 127 for character in unquote(value)):
        return ''
    try:
        parts = urlsplit(value)
        path = reverse('core:catalog_list')
        if parts.scheme or parts.netloc or parts.path != path:
            return ''
        params = dict(parse_qsl(parts.query, keep_blank_values=True, max_num_fields=30))
    except (ValueError, UnicodeError):
        return ''
    filters = {key: item for key, item in normalize_catalog_filters(params).items() if item}
    page = params.get('page', '')
    if page.isascii() and page.isdecimal() and 0 < len(page) <= 7 and 1 <= int(page) <= 1000000:
        filters['page'] = str(int(page))
    query = urlencode(filters)
    fragment = parts.fragment if _PRODUCT_ANCHOR.fullmatch(parts.fragment) else ''
    return path + ('?' + query if query else '') + ('#' + fragment if fragment else '')


def catalog_return(request):
    """An explicit submitted value wins, including an empty or rejected value."""
    if request.method == 'POST' and 'return_to' in request.POST:
        return safe_catalog_return(request.POST.get('return_to'))
    return safe_catalog_return(request.GET.get('return_to'))
