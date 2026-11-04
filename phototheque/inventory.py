"""Explicit shop-photo inventory; no traversal of media, private documents or history."""
from pathlib import Path
import re

from django.apps import apps
from django.conf import settings

from .services import EXTENSIONS, PhotoError, _no_symlinks, _store, read_source


MODEL_FIELDS = {
    'core.Product': ('main_image',),
    'core.ProductImage': ('image',),
    'core.ProductColor': ('image',),
    'accueil.HomepageConfiguration': ('hero_image', 'hero_mobile_image', 'feature_image',
        'family_accessories_image', 'family_fleece_image', 'family_gifts_image', 'store_image'),
    'accueil.HeroSlide': ('image', 'mobile_image'),
    'boutique.ContentPage': ('image', 'draft_image'),
}


def source_metadata(instance, field):
    label = instance._meta.label
    if label == 'core.Product':
        return {'title': instance.name, 'folder_name': 'Produits', 'source_label': 'Produit · Photo principale'}
    if label == 'core.ProductImage':
        return {'title': instance.alt or instance.product.name, 'folder_name': 'Produits', 'source_label': 'Produit · Galerie'}
    if label == 'core.ProductColor':
        return {'title': f'{instance.product.name} · {instance.name}', 'folder_name': 'Produits', 'source_label': 'Produit · Coloris'}
    if label == 'accueil.HomepageConfiguration':
        return {'title': str(instance._meta.get_field(field).verbose_name), 'folder_name': 'Accueil', 'source_label': 'Accueil · Photo de section'}
    if label == 'accueil.HeroSlide':
        return {'title': instance.alt or 'Photo du diaporama', 'folder_name': 'Accueil',
                'source_label': 'Accueil · Photo mobile du diaporama' if field == 'mobile_image' else 'Accueil · Diaporama'}
    return {'title': str(instance), 'folder_name': 'Pages',
            'source_label': 'Page · Brouillon' if field == 'draft_image' else 'Page · Photo publiée'}


def register_reference(kind, name, *, title='', folder_name='', source_label='', actor=None):
    data = read_source(kind, name)
    # A product title may be longer or contain line breaks. The library keeps
    # a readable label without changing the original product or page.
    title = ' '.join(str(title).split())[:160]
    return _store(data, original_name=Path(name).name, title=title, folder_name=folder_name,
                  source_label=source_label, actor=actor)


def _gallery_sources(product):
    if not product.include_imported_gallery or not re.fullmatch(r'[A-Za-z0-9_-]{1,220}', product.slug):
        return
    base = Path(settings.BASE_DIR) / 'core' / 'static'
    for category in ('products', 'best_product', 'femmes', 'hommes', 'enfants'):
        directory = base / 'core' / 'img' / category / product.slug
        try:
            _no_symlinks(directory, base)
            if not directory.is_dir():
                continue
            # Only this product's known imported gallery directory is listed.
            # Unlike the storefront helper, no bytes are read before checking
            # symlinks and the upload size limit.
            for path in sorted(directory.iterdir()):
                if path.suffix.lower() not in EXTENSIONS:
                    continue
                yield {'kind': 'static', 'name': path.relative_to(base).as_posix(),
                       'title': product.name, 'folder_name': 'Produits', 'source_label': 'Produit · Galerie d’origine'}
        except (PhotoError, OSError):
            continue


def iter_sources():
    for label, fields in MODEL_FIELDS.items():
        model = apps.get_model(label)
        query = model.objects.all()
        if label in ('core.ProductImage', 'core.ProductColor'):
            query = query.select_related('product')
        for instance in query.iterator(chunk_size=100):
            for field in fields:
                value = getattr(instance, field)
                if value and value.name:
                    yield {'kind': 'media', 'name': value.name, **source_metadata(instance, field)}
            if label == 'core.Product':
                if instance.static_image:
                    yield {'kind': 'static', 'name': instance.static_image, 'title': instance.name,
                           'folder_name': 'Produits', 'source_label': 'Produit · Photo d’origine'}
                yield from _gallery_sources(instance)
    from accueil.schema import IMAGE_DEFAULTS
    config = apps.get_model('accueil.HomepageConfiguration').objects.filter(pk=1).first()
    for field, name in IMAGE_DEFAULTS.items():
        if name and (config is None or not getattr(config, field, None)):
            title = str(apps.get_model('accueil.HomepageConfiguration')._meta.get_field(field).verbose_name)
            yield {'kind': 'static', 'name': name, 'title': title,
                   'folder_name': 'Accueil', 'source_label': 'Accueil · Photo d’origine'}
    yield {'kind': 'static', 'name': 'core/img/boutique/lelex-2400.jpg',
           'title': 'La boutique de Lélex', 'folder_name': 'Pages', 'source_label': 'Pages · Photo d’origine du magasin'}


def sync_inventory(*, sources=None):
    results = {'created': 0, 'existing': 0, 'failed': 0, 'errors': []}
    seen = set()
    for source in iter_sources() if sources is None else sources:
        identifier = source['kind'], source['name']
        if identifier in seen:
            continue
        seen.add(identifier)
        try:
            _, created = register_reference(**source)
        except (PhotoError, OSError) as error:
            results['failed'] += 1
            results['errors'].append({'source': source.get('source_label', 'Photo'), 'message': str(error)})
        else:
            results['created' if created else 'existing'] += 1
    return results
