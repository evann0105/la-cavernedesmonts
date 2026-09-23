from django import template
from core.product_images import image_variants

register = template.Library()


@register.inclusion_tag('core/partials/product_image.html')
def product_image(product, eager=False, sizes='(max-width: 600px) calc(50vw - 30px), (max-width: 1000px) 33vw, 25vw'):
    variants = [] if product.main_image else image_variants(product.static_image)
    display = next((item for item in variants if item['width'] >= 640), variants[-1] if variants else None)
    return {'src':display['url'] if display else product.image_url,
            'srcset':', '.join(f"{item['url']} {item['width']}w" for item in variants),
            'alt':product.localized_name, 'eager':eager, 'sizes':sizes}
