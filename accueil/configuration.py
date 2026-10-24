"""Read-only home content with safe defaults and a fixed set of link targets."""
from copy import deepcopy
from urllib.parse import urlencode

from django.db.models import Case, IntegerField, Q, Value, When
from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import gettext

from core.models import Product
from core.pricing import with_catalog_prices
from .models import FeaturedHomepageProduct, HeroSlide, HomepageConfiguration
from .schema import (IMAGE_DEFAULTS, IMAGE_FIELDS, OPTIONAL_SECTIONS,
                     SECTION_ANCHORS, SECTION_SPECS, TARGET_CHOICES)


def eligible_featured_products():
    return (Product.objects.filter(is_published=True)
        .exclude(Q(main_image='') | Q(main_image__isnull=True), static_image='')
        .select_related('category').order_by('name', 'pk'))


def _image_metadata(instance, field_name):
    image = getattr(instance, field_name, None) if instance is not None else None
    custom = bool(image and image.name)
    # Dimensions are useful for uploaded portraits too; missing old files must
    # never prevent the storefront or its configuration page from rendering.
    width, height = ((2400, 1594) if field_name == 'store_image' else (2400, 1600))
    if custom:
        try:
            actual_width, actual_height = image.width, image.height
            if type(actual_width) is int and type(actual_height) is int and actual_width > 0 and actual_height > 0:
                width, height = actual_width, actual_height
        except (OSError, ValueError, AttributeError):
            pass
    return {'custom': custom, 'url': image.url if custom else static(IMAGE_DEFAULTS[field_name]),
            'width': width, 'height': height}


def _slide_image_metadata(image):
    if not image or not image.name:
        return None
    width, height = 2400, 1600
    try:
        if type(image.width) is int and type(image.height) is int and image.width > 0 and image.height > 0:
            width, height = image.width, image.height
    except (OSError, ValueError, AttributeError):
        pass
    return {'custom': True, 'url': image.url, 'width': width, 'height': height}


def _slideshow_settings(data, key):
    stored = data.get(key) if isinstance(data.get(key), dict) else {}
    autoplay, interval = stored.get('autoplay'), stored.get('interval')
    return {'autoplay': autoplay if type(autoplay) is bool else True,
            'interval': interval if type(interval) is int and interval in (5, 6, 8, 10) else 6}


def configuration(instance=None):
    """A missing singleton is revision zero, never a reason to write on GET."""
    if instance is None:
        instance = HomepageConfiguration.objects.select_related('updated_by').filter(pk=1).first()
    data = instance.data if instance is not None and isinstance(instance.data, dict) else {}
    stored_values = data.get('values') if isinstance(data.get('values'), dict) else {}
    stored_visible = data.get('visible') if isinstance(data.get('visible'), dict) else {}
    allowed_targets = dict(TARGET_CHOICES)
    values = {}
    for section in SECTION_SPECS:
        overrides = stored_values.get(section['key'])
        overrides = overrides if isinstance(overrides, dict) else {}
        values[section['key']] = {}
        for name, spec in section['fields'].items():
            value = overrides.get(name, spec['default'])
            if (not isinstance(value, str) or len(value) > spec['max_length']
                    or (spec['required'] and not value.strip())
                    or (spec['kind'] == 'target' and value not in allowed_targets)):
                value = spec['default']
            values[section['key']][name] = value
    visible = {'hero': True}
    visible.update({key: stored_visible[key] if type(stored_visible.get(key)) is bool else True
                    for key in OPTIONAL_SECTIONS})
    slides_by_section = {'hero': [], 'feature': []}
    # Both independent carousels share this one bounded-content table read.
    for slide in HeroSlide.objects.filter(configuration_id=1, section__in=slides_by_section).order_by('section', 'position', 'pk'):
        slides = slides_by_section[slide.section]
        if len(slides) >= 5:
            continue
        image = _slide_image_metadata(slide.image)
        if image is not None:
            slides.append({'id': slide.pk, 'position': slide.position, 'image': image,
                'mobile_image': _slide_image_metadata(slide.mobile_image),
                'alt': slide.alt if isinstance(slide.alt, str) and slide.alt.strip() else 'Photo d’ambiance de l’accueil'})
    return {
        'revision': instance.revision if instance is not None else 0,
        'values': values, 'visible': visible,
        'images': {field: _image_metadata(instance, field) for field in IMAGE_FIELDS},
        'featured_ids': list(FeaturedHomepageProduct.objects.order_by('position', 'pk')
                             .values_list('product_id', flat=True)),
        'updated_at': instance.updated_at if instance is not None else None,
        'updated_by': instance.updated_by if instance is not None else None,
        'slides': slides_by_section['hero'],
        'slideshow': _slideshow_settings(data, 'slideshow'),
        'feature_slides': slides_by_section['feature'],
        'feature_slideshow': _slideshow_settings(data, 'feature_slideshow'),
    }


def target_url(target, shop):
    """Resolve an enum, never an arbitrary URL or an untrusted path."""
    categories = {'femmes': 'femmes', 'hommes': 'hommes', 'enfants': 'enfants',
                  'accessoires': 'accessoires', 'accessoires_bebe': 'accessoires-bebe',
                  'polaires_bebe': 'polaires-bebe', 'offres_speciales': 'offres-speciales'}
    if target in categories:
        return reverse('core:product_list') + '?' + urlencode({'categorie': categories[target]})
    routes = {'catalogue': 'core:product_list', 'promotions': 'espace:promotions',
              'nouveautes': 'espace:new', 'boutique': 'core:location',
              'contact': 'espace:contact', 'meilleures_ventes': 'espace:best'}
    if target in routes:
        return reverse(routes[target])
    if target == 'telephone':
        return shop.get('phone_href', '')
    if target == 'email':
        return shop.get('email_href', '')
    return ''


def public_configuration(shop=None):
    if shop is None:
        from boutique.configuration import configuration as shop_configuration
        shop = shop_configuration()
    current = configuration()
    home = {'images': deepcopy(current['images']), 'visible': dict(current['visible'])}
    for section in SECTION_SPECS:
        values = deepcopy(current['values'][section['key']])
        for name, spec in section['fields'].items():
            if spec['kind'] == 'target':
                values[name[:-7] + '_url'] = target_url(values[name], shop)
            else:
                values[name] = gettext(values[name])
        home[section['key']] = values
    if not home['contact']['button_label']:
        target = home['contact']['button_target']
        home['contact']['button_label'] = (shop.get('phone', '') if target == 'telephone'
            else shop.get('email', '') if target == 'email' else gettext('Nous contacter'))
    home['visible_sections'] = [
        {'key': section['key'], 'template': f"accueil/sections/{section['key']}.html"}
        for section in SECTION_SPECS if current['visible'][section['key']]
    ]
    if current['visible']['manifesto']:
        home['scroll_target'] = '#instants'
    else:
        first = next((section['key'] for section in home['visible_sections'] if section['key'] != 'hero'), '')
        home['scroll_target'] = '#' + SECTION_ANCHORS[first] if first else ''
    home['hero_slides'] = [{'primary': True, 'id': None,
        'image': deepcopy(current['images']['hero_image']),
        'mobile_image': deepcopy(current['images']['hero_mobile_image']),
        'alt': home['hero']['image_alt']}]
    home['hero_slides'].extend({'primary': False, 'id': slide['id'],
        'image': deepcopy(slide['image']), 'mobile_image': deepcopy(slide['mobile_image']),
        'alt': slide['alt']} for slide in current['slides'])
    home['slideshow'] = {**current['slideshow'], 'enabled': len(home['hero_slides']) > 1}
    home['feature_slides'] = [{'primary': True, 'id': None,
        'image': deepcopy(current['images']['feature_image']), 'mobile_image': None,
        'alt': home['feature']['image_alt']}]
    home['feature_slides'].extend({'primary': False, 'id': slide['id'],
        'image': deepcopy(slide['image']), 'mobile_image': deepcopy(slide['mobile_image']),
        'alt': slide['alt']} for slide in current['feature_slides'])
    home['feature_slideshow'] = {**current['feature_slideshow'],
        'enabled': bool(current['visible']['feature'] and len(home['feature_slides']) > 1)}
    return home


def featured_products():
    ordered_ids = list(FeaturedHomepageProduct.objects.order_by('position', 'pk')
                       .values_list('product_id', flat=True))
    if ordered_ids:
        eligible_ids = set(eligible_featured_products().filter(pk__in=ordered_ids)
                           .values_list('pk', flat=True))
        ordered_ids = [pk for pk in ordered_ids if pk in eligible_ids]
    if not ordered_ids:
        # Preserve the exact automatic selection used before customization.
        return with_catalog_prices(Product.objects.filter(is_published=True)
            .select_related('category')).order_by('-is_featured', 'id')[:4]
    order = Case(*(When(pk=pk, then=Value(position)) for position, pk in enumerate(ordered_ids)),
                 output_field=IntegerField())
    products = eligible_featured_products().filter(pk__in=ordered_ids)
    return with_catalog_prices(products).order_by(order)[:4]
