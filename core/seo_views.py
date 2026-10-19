"""Public crawl entry points, automatically kept in step with the catalogue."""
from urllib.parse import urlencode
from xml.etree.ElementTree import Element, SubElement, tostring

from django.http import HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_GET

from .models import Category, Product
from .seo import CURATED_INTENTS, PRIVATE_PREFIXES, PUBLIC_PAGES, indexing_enabled, public_absolute_url


@require_GET
def robots(request):
    lines = ['User-agent: *']
    if not indexing_enabled():
        lines.append('Disallow: /')
    else:
        lines.append('Allow: /')
        lines.extend('Disallow: ' + path for path in PRIVATE_PREFIXES)
        lines.extend(['Disallow: /*?*token=', 'Disallow: /*?*q=', 'Disallow: /*/prix/'])
        lines.append('Sitemap: ' + public_absolute_url(reverse('seo_sitemap')))
    response = HttpResponse('\n'.join(lines) + '\n', content_type='text/plain; charset=utf-8')
    response['Cache-Control'] = 'public, max-age=300'
    return response


def sitemap_paths():
    """No fabricated modification dates, and no query/session/token URLs."""
    for name in PUBLIC_PAGES:
        yield reverse(name)
    yield reverse('core:product_list')
    published = Product.objects.filter(is_published=True)
    categories = Category.objects.filter(
        pk__in=published.values('category_id')
    ) | Category.objects.filter(pk__in=published.values('collections__pk'))
    slugs = set(categories.values_list('slug', flat=True))
    for slug in sorted(slugs):
        yield reverse('core:product_list') + '?' + urlencode({'categorie': slug})
    for slug, article_type in CURATED_INTENTS:
        if slug in slugs:
            yield reverse('core:product_list') + '?' + urlencode({'categorie': slug, 'type': article_type})
    if slugs & {'accessoires-bebe', 'polaires-bebe'}:
        yield reverse('core:product_list') + '?' + urlencode({'categorie': 'cadeaux-bebe'})
    for slug in published.order_by('pk').values_list('slug', flat=True).iterator(chunk_size=1000):
        yield reverse('core:product_detail', kwargs={'slug': slug})


@require_GET
def sitemap(request):
    root = Element('urlset', xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    # Even a mistakenly submitted staging sitemap must not advertise its URLs.
    if indexing_enabled():
        seen = set()
        for path in sitemap_paths():
            absolute = public_absolute_url(path)
            if not absolute or absolute in seen:
                continue
            seen.add(absolute)
            node = SubElement(root, 'url')
            SubElement(node, 'loc').text = absolute
    response = HttpResponse(tostring(root, encoding='utf-8', xml_declaration=True), content_type='application/xml; charset=utf-8')
    response['Cache-Control'] = 'public, max-age=300'
    return response
