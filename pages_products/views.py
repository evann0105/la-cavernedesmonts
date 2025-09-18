from django.http import Http404
from django.shortcuts import render
from django.templatetags.static import static
from django.conf import settings
import os

def _img_sort_key(fname: str):
    name = fname.lower()
    i = 0
    while i < len(name) and name[i].isdigit():
        i += 1
    if i > 0:
        try:
            return (0, int(name[:i]), name)
        except ValueError:
            pass
    return (1, name)


def product_page(request, slug: str):
    # Build images list from static folders similar to core preview
    images = []
    base_dir = os.path.join(settings.BASE_DIR, 'core', 'static', 'core', 'img', 'products', slug)
    if os.path.isdir(base_dir):
        for fname in sorted(os.listdir(base_dir), key=_img_sort_key):
            if fname.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                images.append(static(f'core/img/products/{slug}/{fname}'))
    if not images:
        best_dir = os.path.join(settings.BASE_DIR, 'core', 'static', 'core', 'img', 'best_product', slug)
        if os.path.isdir(best_dir):
            for fname in sorted(os.listdir(best_dir), key=_img_sort_key):
                if fname.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    images.append(static(f'core/img/best_product/{slug}/{fname}'))

    # Render a dedicated product page template per slug under pages_products/<slug>.html
    template_path = f'pages_products/{slug}.html'
    context = {
        'slug': slug,
        'name': request.GET.get('name') or slug.replace('-', ' ').title(),
        'images': images,
    }
    try:
        return render(request, template_path, context)
    except Exception:
        # If template doesn't exist, raise 404
        raise Http404()