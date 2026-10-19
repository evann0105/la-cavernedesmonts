"""Build color/size choices from recorded variants without creating stock."""
from urllib.parse import urlsplit


def color_key(value):
    return ' '.join(value.split()).casefold()


def stock_state(variants):
    if any(variant.available > 0 for variant in variants):
        return 'available'
    if not variants or any(variant.quantity is None for variant in variants):
        return 'unknown'
    return 'out'


def build_product_options(product, variants, color_photos):
    sizes, colors, grouped = {}, {}, {}
    for variant in variants:
        size_name = ' '.join(variant.size.split())
        color_name = ' '.join(variant.color.split())
        variant.size_key = color_key(size_name)
        variant.color_key = color_key(color_name)
        size = sizes.setdefault(variant.size_key, {'key': variant.size_key, 'name': size_name, 'available': False})
        size['available'] = size['available'] or variant.available > 0
        grouped.setdefault(variant.color_key, []).append(variant)
        colors.setdefault(variant.color_key, {'key': variant.color_key, 'name': color_name, 'image': ''})
    for photo in color_photos:
        key = color_key(photo.name)
        colors[key] = {'key': key, 'name': photo.name, 'image': photo.image.url}

    main_name = ' '.join(product.main_color.split())
    main_image = product.image_url
    if main_name and main_image:
        key = color_key(main_name)
        option = colors.pop(key, {'key': key, 'name': main_name, 'image': ''})
        if not option['image']:
            option['image'] = main_image
        colors = {key: option, **colors}
    elif main_image and (color_photos or len(colors) > 1):
        pictured = any(option['image'] and urlsplit(option['image']).path == urlsplit(main_image).path for option in colors.values())
        if not pictured:
            # A photograph without an identified color never implies sizes or quantities.
            colors = {'__main_photo__': {'key': '__main_photo__', 'name': 'Coloris d’origine', 'image': main_image}, **colors}

    for key, option in colors.items():
        option['state'] = stock_state(grouped.get(key, []))
    return {'sizes': list(sizes.values()), 'colors': list(colors.values())}
