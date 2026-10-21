"""Automatic, factual metadata and a single explicit indexing policy.

Canonical URLs come from deployment configuration, never an untrusted Host or
forwarded header. Private routes are deliberately outside the public allowlist.
"""
import ipaddress
import json
import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.templatetags.static import static
from django.urls import reverse
from django.utils.html import strip_tags


SHOP_NAME = 'La Caverne des Monts'
DEFAULT_TITLE = 'La Caverne des Monts — Vêtements et accessoires à Lélex'
DEFAULT_DESCRIPTION = ('Polaires, vestes, vêtements et accessoires pour femme, homme et enfant. '
                       'Découvrez La Caverne des Monts, boutique à Lélex dans le Jura.')
PUBLIC_PAGES = {
    'accueil:home': (DEFAULT_TITLE, DEFAULT_DESCRIPTION),
    'core:location': ('Notre boutique à Lélex — La Caverne des Monts',
                      'Retrouvez La Caverne des Monts à Lélex : adresse, contact et horaires du magasin de vêtements et accessoires pour toute la famille.'),
    'espace:contact': ('Contact et conseils — La Caverne des Monts à Lélex',
                       'Contactez La Caverne des Monts à Lélex pour une question sur un vêtement, une taille, une commande ou votre visite en boutique.'),
    'espace:about': ('La boutique et son histoire — La Caverne des Monts',
                     'Découvrez La Caverne des Monts, boutique de vêtements et accessoires à Lélex, au cœur du Jura.'),
    'espace:promotions': ('Promotions vêtements et accessoires — La Caverne des Monts',
                          'Découvrez les réductions en cours sur les vêtements et accessoires de La Caverne des Monts. Consultez les prix et disponibilités de chaque article.'),
    'espace:new': ('Nouveaux vêtements et accessoires — La Caverne des Monts',
                    'Les nouveautés de La Caverne des Monts : vêtements et accessoires pour la famille, à découvrir en ligne et dans notre boutique de Lélex.'),
    'espace:best': ('La sélection de la boutique — La Caverne des Monts',
                     'Retrouvez la sélection de vêtements et accessoires proposée par La Caverne des Monts à Lélex.'),
    'espace:sitemap': ('Plan du site — La Caverne des Monts',
                        'Accédez aux collections, fiches produits et informations de La Caverne des Monts à Lélex.'),
    'espace:legal_notice': ('Mentions légales — La Caverne des Monts',
                            'Identité, coordonnées et informations légales de La Caverne des Monts à Lélex.'),
    'espace:sales_terms': ('Conditions générales de vente — La Caverne des Monts',
                           'Consultez les conditions générales de vente de La Caverne des Monts : commande, paiement, livraison, retours et garanties.'),
    'espace:privacy': ('Confidentialité et données personnelles — La Caverne des Monts',
                       'Découvrez comment La Caverne des Monts utilise et protège vos données personnelles et comment exercer vos droits.'),
    'espace:cookies': ('Cookies — La Caverne des Monts',
                       'Informations sur les cookies utilisés par La Caverne des Monts et sur vos choix.'),
    'espace:withdrawal': ('Rétractation, retours et garanties — La Caverne des Monts',
                           'Informations sur la rétractation, les retours, les remboursements et les garanties des achats chez La Caverne des Monts.'),
    'espace:accessibility': ('Accessibilité — La Caverne des Monts',
                             'Informations sur l’accessibilité du site de La Caverne des Monts et le contact pour signaler une difficulté.'),
    'espace:review_policy': ('Présentation des avis — La Caverne des Monts',
                             'Découvrez comment les avis présentés sur le site de La Caverne des Monts sont sélectionnés et affichés.'),
}
CURATED_INTENTS = {
    ('femmes', 'polaires'): ('Polaires femme', 'Découvrez les polaires femme de La Caverne des Monts : coupes, tailles, couleurs et caractéristiques des modèles proposés à Lélex.'),
    ('hommes', 'vestes'): ('Vestes et blousons homme', 'Découvrez les vestes et blousons homme de La Caverne des Monts : modèles, tailles, couleurs et caractéristiques pour choisir votre vêtement.'),
}
BABY_TITLE = 'Cadeaux bébé'
BABY_DESCRIPTION = 'Découvrez les vêtements et accessoires bébé de La Caverne des Monts : des idées de cadeaux et des articles pour les premiers mois.'
PRIVATE_PREFIXES = ('/admin/', '/gestion/', '/account/', '/accounts/', '/paiement/',
                    '/panier/', '/retours/', '/mes-retours/', '/retractation/commande/',
                    '/historique-commandes/', '/commandes/', '/factures/', '/facturation/', '/avoirs/',
                    '/adresses/', '/identite/', '/mes-donnees/', '/reduction/',
                    '/mon-compte/', '/preview/', '/page-produit/')
PRIVATE_QUERY_KEYS = {'token', 'signature', 'secret', 'access_token', 'key', 'code'}
TRACKING_KEYS = {'gclid', 'fbclid', 'msclkid'}


def clean_text(value):
    return re.sub(r'\s+', ' ', strip_tags(str(value or ''))).strip()


def json_ld(value):
    """Safe inside a script element; do not use HTML entities inside JSON."""
    if not value:
        return ''
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            .replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))


def _safe_origin(value, *, https_only=False):
    if not isinstance(value, str) or re.search(r'[\\\x00-\x20\x7f]', value):
        return ''
    try:
        parts = urlsplit(value)
        if (parts.scheme not in (('https',) if https_only else ('https', 'http'))
                or not parts.hostname or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or parts.path not in ('', '/')):
            return ''
        host = parts.hostname.encode('idna').decode('ascii').lower().rstrip('.')
        if not host or not re.fullmatch(r'[a-z0-9.:-]+', host):
            return ''
        if ':' in host:
            ipaddress.ip_address(host)
        elif any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.')):
            return ''
        port = parts.port
        netloc = f'[{host}]' if ':' in host else host
        if port is not None and port != (443 if parts.scheme == 'https' else 80):
            netloc += f':{port}'
        return urlunsplit((parts.scheme, netloc, '', '', ''))
    except (ValueError, UnicodeError):
        return ''


def site_origin():
    """Configured origin, including local URLs for a truthful development preview."""
    return _safe_origin(getattr(settings, 'SITE_URL', ''))


def indexing_enabled():
    if settings.DEBUG or not getattr(settings, 'SEO_INDEXING_ENABLED', False):
        return False
    origin = _safe_origin(getattr(settings, 'SITE_URL', ''), https_only=True)
    if not origin:
        return False
    host = urlsplit(origin).hostname
    if host == 'localhost' or '.' not in host or host.endswith(('.localhost', '.local', '.localdomain', '.internal', '.lan', '.home', '.home.arpa', '.test', '.invalid', '.example')):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


def indexing_allowed():
    return indexing_enabled()


def public_absolute_url(path, *, allow_external=False):
    """Return a safe configured-origin URL, or an explicitly allowed HTTPS asset."""
    if not path or not isinstance(path, str) or re.search(r'[\\\x00-\x20\x7f]', path):
        return ''
    origin = site_origin()
    if not origin:
        return ''
    try:
        parts = urlsplit(path)
        if parts.scheme or parts.netloc:
            candidate_origin = _safe_origin(urlunsplit((parts.scheme, parts.netloc, '', '', '')))
            if not candidate_origin or parts.username is not None or parts.password is not None:
                return ''
            if candidate_origin != origin and not (allow_external and parts.scheme == 'https'):
                return ''
            return urlunsplit((parts.scheme, parts.netloc, parts.path or '/', parts.query, ''))
        if not path.startswith('/') or path.startswith('//'):
            return ''
        return origin + urlunsplit(('', '', parts.path, parts.query, ''))
    except ValueError:
        return ''


def _tracking_key(key):
    return key.startswith('utm_') or key in TRACKING_KEYS


def _has_noncanonical_query(request, name):
    for key, values in request.GET.lists():
        if _tracking_key(key) or not any(values):
            continue
        if (name == 'core:product_detail' and key == 'variante'
                and getattr(request, '_seo_variant_valid', False)
                and len(values) == 1 and re.fullmatch(r'[0-9]{1,20}', values[0]) and values[0].lstrip('0')):
            continue
        return True
    return False


def _private_request(request):
    return (request.path.startswith(PRIVATE_PREFIXES)
            or any(key.casefold() in PRIVATE_QUERY_KEYS for key in request.GET)
            or request.path.endswith('/prix/'))


def _catalog_metadata(request):
    """Index stable collections; do not index arbitrary internal search/facets."""
    from .models import Category
    category_values = list(dict.fromkeys(value for value in request.GET.getlist('categorie') if value))
    category_slug = category_values[0] if len(category_values) == 1 else ''
    category = Category.objects.filter(slug=category_slug).first() if category_slug and category_slug != 'cadeaux-bebe' else None
    valid_category = category is not None or category_slug == 'cadeaux-bebe'
    params = {}
    if valid_category:
        params['categorie'] = category_slug
    title = clean_text(category.name) if category else ('Cadeaux bébé' if category_slug == 'cadeaux-bebe' else 'Vêtements et accessoires pour toute la famille')
    description = (f'Découvrez la collection {clean_text(category.name).lower()} de La Caverne des Monts : vêtements et accessoires, tailles, couleurs et caractéristiques des articles.'
                   if category else (BABY_DESCRIPTION if category_slug == 'cadeaux-bebe' else DEFAULT_DESCRIPTION))
    type_values = list(dict.fromkeys(value for value in request.GET.getlist('type') if value))
    article_type = type_values[0] if len(type_values) == 1 else ''
    curated = CURATED_INTENTS.get((category_slug, article_type))
    if curated:
        title, description = curated
        params['type'] = article_type
    allowed = {'categorie', 'type', 'page'}
    has_facets = any(values for key, values in request.GET.lists()
                     if key not in allowed and not _tracking_key(key) and any(values))
    if (len(category_values) > 1 or (category_slug and not valid_category)
            or len(type_values) > 1 or (article_type and not curated)):
        has_facets = True
    raw_page = request.GET.get('page', '1')
    try:
        page_number = int(raw_page)
        if page_number < 1 or len(set(request.GET.getlist('page'))) > 1:
            has_facets = True
    except (TypeError, ValueError):
        page_number = 1
        has_facets = True
    page_number = getattr(request, '_seo_page_number', page_number)
    if page_number >= 2 and not has_facets:
        params['page'] = page_number
        title = f'{title} — Page {page_number}'
    path = reverse('core:product_list')
    if params:
        path += '?' + urlencode(params)
    return title + ' — ' + SHOP_NAME, description, path, not has_facets


def _store_graph():
    """Only information already displayed publicly, with no invented reviews/geo."""
    origin = site_origin()
    if not origin:
        return None
    from boutique.configuration import configuration
    from boutique.services import public_hours
    current = configuration()
    shop = {'@type': 'ClothingStore', '@id': origin + '/#boutique', 'name': SHOP_NAME,
            'url': origin + '/', 'address': {
                '@type': 'PostalAddress',
                'streetAddress': ', '.join(filter(None, (current['store_address_line1'], current['store_address_line2']))),
                'postalCode': current['store_postal_code'], 'addressLocality': current['store_city'],
                'addressCountry': current['store_country'],
            }, 'telephone': current['phone'], 'email': current['email']}
    logo = public_absolute_url(static('core/img/logo.png'), allow_external=True)
    if logo:
        shop['logo'] = logo
    hours = public_hours()
    specifications = []
    weekday_names = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
    for weekday, day in enumerate(hours['opening_days']):
        if not day['known']:
            continue
        periods = day['periods'] if not day['closed'] else [{'start': None, 'end': None}]
        for period in periods:
            spec = {'@type': 'OpeningHoursSpecification', 'dayOfWeek': 'https://schema.org/' + weekday_names[weekday],
                    'opens': period['start'].strftime('%H:%M') if period['start'] else '00:00',
                    'closes': period['end'].strftime('%H:%M') if period['end'] else '00:00'}
            if hours['opening_period']:
                spec['validFrom'] = hours['opening_period'].start_date.isoformat()
                spec['validThrough'] = hours['opening_period'].end_date.isoformat()
            specifications.append(spec)
    if specifications:
        shop['openingHoursSpecification'] = specifications
    return {'@context': 'https://schema.org', '@graph': [
        {'@type': 'WebSite', '@id': origin + '/#site', 'url': origin + '/', 'name': SHOP_NAME, 'inLanguage': 'fr-FR'},
        {key: value for key, value in shop.items() if value},
    ]}


def page_seo(request, product=None, product_metadata=None, structured_data=None):
    match = request.resolver_match
    name = match.view_name if match else ''
    private = _private_request(request)
    eligible = name in PUBLIC_PAGES or name in {'core:product_list', 'core:product_detail'}
    title, description = PUBLIC_PAGES.get(name, (DEFAULT_TITLE, DEFAULT_DESCRIPTION))
    path = request.path
    if name == 'core:product_list' and not private:
        title, description, path, eligible = _catalog_metadata(request)
    elif name == 'espace:terms':
        path = reverse('espace:sales_terms')
    elif name == 'core:location_legacy':
        path = reverse('core:location')
    elif _has_noncanonical_query(request, name):
        eligible = False
    image = public_absolute_url(static('core/img/hero/montagne-1536.webp'), allow_external=True)
    if product is not None:
        eligible = (name == 'core:product_detail' and bool(product.is_published)
                    and not _has_noncanonical_query(request, name))
        path = reverse('core:product_detail', kwargs={'slug': product.slug})
        meta = product_metadata or {}
        title = meta.get('title') or f'{clean_text(product.localized_name)} — {SHOP_NAME}'
        description = meta.get('description') or clean_text(product.localized_description) or DEFAULT_DESCRIPTION
        image = public_absolute_url(meta.get('image') or product.image_url, allow_external=True)
    canonical = '' if private else public_absolute_url(path)
    indexable = indexing_enabled() and eligible and not private
    robots = ('index, follow, max-image-preview:large' if indexable else
              ('noindex, nofollow' if private or not indexing_enabled() else 'noindex, follow'))
    if structured_data is None and name in {'accueil:home', 'core:location', 'espace:contact'} and not private:
        structured_data = _store_graph()
    return {'title': clean_text(title), 'description': clean_text(description), 'canonical': canonical,
            'url': canonical, 'robots': robots, 'indexable': indexable,
            'og_type': 'product' if product is not None else 'website', 'image': image,
            'json_ld': json_ld(structured_data) if not private else ''}


def metadata(request):
    return {'seo': page_seo(request)}


def response_is_indexable(request, response):
    name = request.resolver_match.view_name if request.resolver_match else ''
    if not indexing_enabled() or _private_request(request) or response.status_code != 200:
        return False
    # Public product photographs must remain eligible for Google Images. Other
    # media (documents, exports, uploaded proofs) never inherit this exception.
    if response.get('Content-Type', '').split(';')[0].startswith('image/') and (
        request.path.startswith(('/static/core/img/', '/media/products/'))
    ):
        return True
    if name in {'seo_robots', 'seo_sitemap'}:
        return True
    if name == 'core:product_list':
        return _catalog_metadata(request)[3]
    return (name in PUBLIC_PAGES or name == 'core:product_detail') and (
        not _has_noncanonical_query(request, name))


def noindex_directive(request, response):
    name = request.resolver_match.view_name if request.resolver_match else ''
    if (indexing_enabled() and not _private_request(request) and response.status_code == 200
            and (name in PUBLIC_PAGES or name in {'core:product_list', 'core:product_detail'})):
        return 'noindex, follow'
    return 'noindex, nofollow'
