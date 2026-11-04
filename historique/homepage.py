"""Whole-home snapshots and restoration, independent from catalogue/stock edits."""
from copy import deepcopy

from django.db import transaction
from django.shortcuts import render
from django.utils.cache import patch_vary_headers
from django.views.decorators.clickjacking import xframe_options_sameorigin

from accueil.configuration import (configuration, eligible_featured_products,
                                    featured_products, public_configuration)
from accueil.models import FeaturedHomepageProduct, HeroSlide, HomepageConfiguration
from accueil.schema import IMAGE_DEFAULTS, IMAGE_FIELDS, IMAGE_LABELS, SECTION_SPECS, SECTIONS, TARGET_CHOICES
from core.homepage import WORLD_CARDS, eligible_products, world_cards
from core.models import HomepageSelection, Product

from .exceptions import HistoryConflict, HistoryError
from .media import asset_name, asset_warnings, image_url, safe_name
from .models import HistoryVersion
from .services import authorized, fingerprint, record

SCHEMA = 1
SLOTS = tuple(spec['slot'] for spec in WORLD_CARDS)


def _image(kind, name):
    return {'kind': kind, 'name': name}


def snapshot_homepage():
    instance = HomepageConfiguration.objects.filter(pk=1).first()
    current = configuration(instance=instance)
    images = {}
    for name in IMAGE_FIELDS:
        stored = getattr(instance, name).name if instance else ''
        images[name] = _image('media', stored) if stored else _image('static', IMAGE_DEFAULTS[name])
    slides = [
        {'section': slide.section, 'position': slide.position, 'alt': slide.alt,
         'image': _image('media', slide.image.name),
         'mobile_image': _image('media', slide.mobile_image.name) if slide.mobile_image.name else None}
        for slide in HeroSlide.objects.filter(configuration_id=1).order_by('section', 'position', 'pk')
    ]
    return {
        'schema': SCHEMA, 'values': deepcopy(current['values']),
        'visible': dict(current['visible']), 'images': images, 'slides': slides,
        'slideshow': dict(current['slideshow']), 'feature_slideshow': dict(current['feature_slideshow']),
        'featured_ids': list(current['featured_ids']),
        # Absence retains the original preferred slug; an explicit null uses
        # the first eligible product. These are distinct automatic modes.
        'collections': dict(HomepageSelection.objects.filter(slot__in=SLOTS).order_by('slot').values_list('slot', 'product_id')),
    }


def _assets(snapshot):
    assets = list(snapshot['images'].values())
    for slide in snapshot['slides']:
        assets.append(slide['image'])
        if slide['mobile_image']:
            assets.append(slide['mobile_image'])
    return assets


def record_homepage(actor=None, label='', source='edit', restored_from=None):
    snapshot = snapshot_homepage()
    return record('homepage', 1, snapshot, actor=actor, object_label='Accueil de la boutique',
                  label=label, source=source, restored_from=restored_from, assets=_assets(snapshot))


def _snapshot(version):
    if version.scope != 'homepage' or version.object_id != 1:
        raise HistoryError('Cette version n’appartient pas à l’accueil.')
    snapshot = version.snapshot
    if not isinstance(snapshot, dict) or fingerprint(snapshot) != version.fingerprint:
        raise HistoryError('Cette version est endommagée. Aucune restauration n’a été effectuée.')
    _validate_snapshot(snapshot)
    return snapshot


def _validate_image(value, optional=False):
    if value is None and optional:
        return
    if not isinstance(value, dict) or set(value) != {'kind', 'name'} or value['kind'] not in {'media', 'static'}:
        raise HistoryError('Une photo de cette version possède une référence invalide.')
    safe_name(value['name'])


def _product_id(value, nullable=False):
    return value is None and nullable or type(value) is int and 0 < value <= 9223372036854775807


def _validate_snapshot(snapshot):
    if (set(snapshot) != {'schema', 'values', 'visible', 'images', 'slides', 'slideshow',
                          'feature_slideshow', 'featured_ids', 'collections'}
            or type(snapshot['schema']) is not int or snapshot['schema'] != SCHEMA):
        raise HistoryError('Le format de cette version d’accueil n’est pas pris en charge.')
    if not isinstance(snapshot['values'], dict) or set(snapshot['values']) != set(SECTIONS):
        raise HistoryError('Les textes de cette version sont incomplets.')
    allowed_targets = dict(TARGET_CHOICES)
    for section in SECTION_SPECS:
        values = snapshot['values'][section['key']]
        if not isinstance(values, dict) or set(values) != set(section['fields']):
            raise HistoryError('Un bloc de cette version est incomplet.')
        for name, spec in section['fields'].items():
            value = values[name]
            if (not isinstance(value, str) or len(value) > spec['max_length']
                    or spec['required'] and not value.strip()
                    or spec['kind'] == 'target' and value not in allowed_targets):
                raise HistoryError('Un texte ou un bouton de cette version n’est plus valide.')
    if (not isinstance(snapshot['visible'], dict) or set(snapshot['visible']) != set(SECTIONS)
            or any(type(value) is not bool for value in snapshot['visible'].values())
            or snapshot['visible']['hero'] is not True):
        raise HistoryError('La visibilité des blocs de cette version est invalide.')
    if not isinstance(snapshot['images'], dict) or set(snapshot['images']) != set(IMAGE_FIELDS):
        raise HistoryError('Les photos de cette version sont incomplètes.')
    for image in snapshot['images'].values():
        _validate_image(image)
    for name in ('slideshow', 'feature_slideshow'):
        settings = snapshot[name]
        if (not isinstance(settings, dict) or set(settings) != {'autoplay', 'interval'}
                or type(settings['autoplay']) is not bool or type(settings['interval']) is not int
                or settings['interval'] not in (5, 6, 8, 10)):
            raise HistoryError('Les réglages d’un diaporama de cette version sont invalides.')
    slides = snapshot['slides']
    if not isinstance(slides, list) or len(slides) > 10:
        raise HistoryError('Le nombre de photos de cette version est invalide.')
    positions = {'hero': set(), 'feature': set()}
    for slide in slides:
        if (not isinstance(slide, dict) or set(slide) != {'section', 'position', 'alt', 'image', 'mobile_image'}
                or not isinstance(slide['section'], str) or slide['section'] not in positions
                or type(slide['position']) is not int or not 1 <= slide['position'] <= 5
                or slide['position'] in positions[slide['section']]
                or not isinstance(slide['alt'], str) or not slide['alt'].strip() or len(slide['alt']) > 200):
            raise HistoryError('Une photo du diaporama de cette version est invalide.')
        positions[slide['section']].add(slide['position'])
        _validate_image(slide['image'])
        _validate_image(slide['mobile_image'], optional=True)
    ids = snapshot['featured_ids']
    if not isinstance(ids, list) or len(ids) > 4 or any(not _product_id(pk) for pk in ids) or len(set(ids)) != len(ids):
        raise HistoryError('La sélection des produits de cette version est invalide.')
    choices = snapshot['collections']
    if (not isinstance(choices, dict) or set(choices) - set(SLOTS)
            or any(not _product_id(pk, nullable=True) for pk in choices.values())):
        raise HistoryError('Les cartes des collections de cette version sont invalides.')


def _product_problems(snapshot):
    messages = []
    requested = snapshot['featured_ids']
    eligible = set(eligible_featured_products().filter(pk__in=requested).values_list('pk', flat=True))
    for pk in requested:
        if pk not in eligible:
            messages.append(f'Le produit mis en avant n° {pk} est supprimé, masqué ou sans photo. Cette sélection ne peut pas être restaurée telle quelle.')
    for spec in WORLD_CARDS:
        pk = snapshot['collections'].get(spec['slot'])
        if pk is not None and not eligible_products(spec['slot']).filter(pk=pk).exists():
            messages.append(f'Le produit n° {pk} de la carte {spec["label"]} n’est plus disponible dans cette collection. Cette carte ne peut pas être restaurée telle quelle.')
    return messages


def restore_warnings(version):
    snapshot = _snapshot(version)
    return [*asset_warnings(version), *_product_problems(snapshot),
            'Les prix, stocks et fiches produits restent ceux du catalogue actuel. Les choix automatiques restent automatiques.']


def validate_restore(version):
    snapshot = _snapshot(version)
    problems = _product_problems(snapshot)
    if problems:
        raise HistoryError(' '.join(problems))
    # Restoration pins every image to its retained copy, even an old default
    # shipped as a static file. Nothing depends on future static-file changes.
    for image in _assets(snapshot):
        asset_name(version, image['kind'], image['name'], prefer_archive=True)
    return snapshot


def _retained(version, image):
    return asset_name(version, image['kind'], image['name'], prefer_archive=True)[1] if image else ''


@transaction.atomic
def restore_homepage(version, *, actor, expected_fingerprint):
    authorized(actor)
    from accueil.services import _locked, _save
    instance, is_new = _locked(None)
    current = snapshot_homepage()
    if not isinstance(expected_fingerprint, str) or fingerprint(current) != expected_fingerprint:
        raise HistoryConflict('L’accueil a changé depuis votre vérification. Comparez à nouveau les versions avant de restaurer.')
    saved = HistoryVersion.objects.filter(pk=version.pk, scope='homepage', object_id=1).first()
    if saved is None:
        raise HistoryError('Cette version n’existe pas dans l’historique de l’accueil.')
    snapshot = validate_restore(saved)
    # Products are never recreated or republished. Hold their identities while
    # replacing only home-owned references in the same transaction.
    ids = set(snapshot['featured_ids']) | {pk for pk in snapshot['collections'].values() if pk is not None}
    list(Product.objects.select_for_update().filter(pk__in=sorted(ids)).order_by('pk'))
    problems = _product_problems(snapshot)
    if problems:
        raise HistoryError(' '.join(problems))
    record_homepage(actor=actor, label='Accueil avant restauration', source='checkpoint')
    instance.data = {key: deepcopy(snapshot[key]) for key in ('values', 'visible', 'slideshow', 'feature_slideshow')}
    for name, image in snapshot['images'].items():
        setattr(instance, name, _retained(saved, image))
    _save(instance, is_new, actor, f'Restauration de la version {saved.sequence}')
    HeroSlide.objects.filter(configuration_id=1).delete()
    for slide in snapshot['slides']:
        HeroSlide.objects.create(configuration=instance, section=slide['section'], position=slide['position'],
            alt=slide['alt'], image=_retained(saved, slide['image']), mobile_image=_retained(saved, slide['mobile_image']))
    FeaturedHomepageProduct.objects.all().delete()
    FeaturedHomepageProduct.objects.bulk_create([
        FeaturedHomepageProduct(product_id=pk, position=position)
        for position, pk in enumerate(snapshot['featured_ids'], 1)])
    HomepageSelection.objects.filter(slot__in=SLOTS).delete()
    HomepageSelection.objects.bulk_create([
        HomepageSelection(slot=slot, product_id=pk) for slot, pk in snapshot['collections'].items()])
    return record_homepage(actor=actor, label=f'Restauration de la version {saved.sequence}',
                           source='restore', restored_from=saved)


@transaction.atomic
def save_collections(cleaned_data, *, actor):
    """Bring the original three-card form under the same revision and lock."""
    authorized(actor)
    from accueil.services import _locked, _revision, _save, _validate
    from core.catalog_forms import HomepageSelectionForm
    expected = _revision(cleaned_data)
    instance, is_new = _locked(expected)
    values = {'revision': expected}
    for slot in SLOTS:
        value = cleaned_data.get(slot)
        values[slot] = value.pk if isinstance(value, Product) else value
    validated = _validate(HomepageSelectionForm(values))
    record_homepage(actor=actor, label='Accueil avant modification des cartes', source='baseline')
    for slot in SLOTS:
        HomepageSelection.objects.update_or_create(slot=slot, defaults={'product': validated[slot]})
    _save(instance, is_new, actor, 'Cartes Femme, Homme et Enfant')
    return record_homepage(actor=actor, label='Cartes Femme, Homme et Enfant')


def _preview_configuration(version, snapshot):
    instance = HomepageConfiguration(data={key: deepcopy(snapshot[key])
        for key in ('values', 'visible', 'slideshow', 'feature_slideshow')})
    current = configuration(instance=instance, slide_rows=[], featured_ids=snapshot['featured_ids'])

    def metadata(image):
        if image is None:
            return None
        url = image_url(image['name'], image['kind'], version=version)
        return {'custom': True, 'url': url, 'width': 2400, 'height': 1600, 'missing': not bool(url)}

    current['images'] = {name: metadata(image) for name, image in snapshot['images'].items()}
    for section, key in (('hero', 'slides'), ('feature', 'feature_slides')):
        current[key] = [{'id': index + 1, 'position': row['position'], 'alt': row['alt'],
                         'image': metadata(row['image']), 'mobile_image': metadata(row['mobile_image'])}
                        for index, row in enumerate(snapshot['slides']) if row['section'] == section]
    return current


@xframe_options_sameorigin
def preview_response(request, version):
    authorized(request.user)
    snapshot = _snapshot(version)
    from boutique.configuration import configuration as shop_configuration
    shop = shop_configuration()
    current = _preview_configuration(version, snapshot)
    content = public_configuration(shop=shop, current=current)
    request._homepage_configuration = content
    # Manual references which have become ineligible are left empty in the
    # preview, never silently replaced by another public product.
    products = list(featured_products(ordered_ids=snapshot['featured_ids']))
    if snapshot['featured_ids']:
        products = [product for product in products if product.pk in snapshot['featured_ids']]
    cards = world_cards(chosen=snapshot['collections'])
    for card in cards:
        chosen = snapshot['collections'].get(card['slot'])
        if chosen is not None and (not card['product'] or card['product'].pk != chosen):
            card['product'] = None
    response = render(request, 'accueil/home.html', {
        'home': content, 'shop': shop, 'world_cards': cards, 'products': products,
        'history_preview': True, 'history_warnings': restore_warnings(version),
    })
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'same-origin'
    response['X-Content-Type-Options'] = 'nosniff'
    patch_vary_headers(response, ('Cookie',))
    return response


def describe_changes(before_snapshot, after_snapshot, *, before_version=None, after_version=None):
    _validate_snapshot(after_snapshot)
    if before_snapshot is not None:
        _validate_snapshot(before_snapshot)
    before = before_snapshot or {}
    result = []

    def photo(image, version, label):
        if not image:
            return {'url': '', 'alt': 'Aucune photo', 'missing': False}
        url = image_url(image['name'], image['kind'], version=version)
        return {'url': url, 'alt': label, 'missing': not bool(url)}

    def change(rows, label, old, new, kind='text'):
        if old != new:
            rows.append({'label': label, 'before': old, 'after': new, 'kind': kind})

    for section in SECTION_SPECS:
        key = section['key']; rows = []
        change(rows, 'Affichage du bloc', 'Affiché' if before.get('visible', {}).get(key) else 'Masqué',
               'Affiché' if after_snapshot['visible'][key] else 'Masqué')
        for name, spec in section['fields'].items():
            change(rows, spec['label'], before.get('values', {}).get(key, {}).get(name, ''), after_snapshot['values'][key][name])
        for name in section['images']:
            old, new = before.get('images', {}).get(name), after_snapshot['images'][name]
            if old != new:
                rows.append({'label': IMAGE_LABELS[name], 'kind': 'image',
                    'before': photo(old, before_version, IMAGE_LABELS[name]),
                    'after': photo(new, after_version, IMAGE_LABELS[name])})
        if key in ('hero', 'feature'):
            setting = 'slideshow' if key == 'hero' else 'feature_slideshow'
            old_settings, new_settings = before.get(setting, {}), after_snapshot[setting]
            change(rows, 'Défilement automatique', 'Oui' if old_settings.get('autoplay') else 'Non', 'Oui' if new_settings['autoplay'] else 'Non')
            change(rows, 'Intervalle entre les photos', str(old_settings.get('interval', '')) + ' secondes', str(new_settings['interval']) + ' secondes')
            old_slides = [slide for slide in before.get('slides', []) if slide['section'] == key]
            new_slides = [slide for slide in after_snapshot['slides'] if slide['section'] == key]
            for index in range(max(len(old_slides), len(new_slides))):
                old = old_slides[index] if index < len(old_slides) else {}
                new = new_slides[index] if index < len(new_slides) else {}
                for name, label in (('image', 'Photo'), ('mobile_image', 'Cadrage téléphone')):
                    if old.get(name) != new.get(name):
                        rows.append({'label': f'{label} supplémentaire {index + 1}', 'kind': 'image',
                                     'before': photo(old.get(name), before_version, old.get('alt', label)),
                                     'after': photo(new.get(name), after_version, new.get('alt', label))})
                change(rows, f'Description photo {index + 1}', old.get('alt', ''), new.get('alt', ''))
                change(rows, f'Position photo {index + 1}', str(old.get('position', '')), str(new.get('position', '')))
        if rows:
            result.append({'title': section['title'], 'rows': rows})
    ids = set(before.get('featured_ids', [])) | set(after_snapshot['featured_ids'])
    ids |= {pk for pk in before.get('collections', {}).values() if pk}
    ids |= {pk for pk in after_snapshot['collections'].values() if pk}
    names = dict(Product.objects.filter(pk__in=ids).values_list('pk', 'name'))
    label = lambda pk: f'{names.get(pk, "Produit supprimé")} (n° {pk})'
    rows = []
    change(rows, 'Ordre des produits mis en avant', [label(pk) for pk in before.get('featured_ids', [])] or ['Sélection automatique'],
           [label(pk) for pk in after_snapshot['featured_ids']] or ['Sélection automatique'], kind='list')
    for spec in WORLD_CARDS:
        def choice(data):
            choices = data.get('collections', {})
            if spec['slot'] not in choices:
                return 'Choix automatique d’origine'
            pk = choices[spec['slot']]
            return label(pk) if pk else 'Choix automatique'
        change(rows, f'Carte {spec["label"]}', choice(before), choice(after_snapshot))
    if rows:
        result.append({'title': 'Produits et cartes des collections', 'rows': rows})
    return result
