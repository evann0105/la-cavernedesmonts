from django.shortcuts import render, get_object_or_404
from .models import Product
from django.templatetags.static import static
import os
from django.conf import settings

"""Home moved to 'accueil' app."""


def product_list(request):
	products = (
		Product.objects.prefetch_related('images')
		.order_by('-is_featured', '-created_at')[:12]
	)
	return render(request, 'core/product_list.html', {"products": products})


def product_detail(request, slug: str):
	product = get_object_or_404(Product, slug=slug)
	images = product.images.all() if hasattr(product, 'images') else []
	return render(request, 'core/product_detail.html', {"product": product, "images": images})


def product_preview(request, slug: str):
	# Static-based preview using files under core/static/core/img/products/<slug>/
	base_dir = os.path.join(settings.BASE_DIR, 'core', 'static', 'core', 'img', 'products', slug)
	image_files = []

	# Allow passing a specific image path via query param for convenience
	q_img = request.GET.get('img')
	if q_img:
		# very small safety: prevent directory traversal and restrict to core/img/
		if '..' not in q_img and q_img.startswith('core/img/'):
			image_files.append(static(q_img))
	if os.path.isdir(base_dir):
		for fname in sorted(os.listdir(base_dir)):
			if fname.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
				image_files.append(static(f'core/img/products/{slug}/{fname}'))

	# Also look under best_product folder for bestsellers content
	if not image_files:
		best_dir = os.path.join(settings.BASE_DIR, 'core', 'static', 'core', 'img', 'best_product', slug)
		if os.path.isdir(best_dir):
			for fname in sorted(os.listdir(best_dir)):
				if fname.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
					image_files.append(static(f'core/img/best_product/{slug}/{fname}'))

	# Fallback to a single image using the previous flat files if exist
	if not image_files:
		flat = os.path.join(settings.BASE_DIR, 'core', 'static', 'core', 'img', 'products', f'{slug}.png')
		if os.path.exists(flat):
			image_files.append(static(f'core/img/products/{slug}.png'))

	context = {
		'preview': {
			'name': request.GET.get('name') or slug.replace('-', ' ').title(),
			'price': request.GET.get('price', ''),
			'description': request.GET.get('desc', ''),
			'images': image_files,
			'slug': slug,
		}
	}
	return render(request, 'core/product_preview.html', context)
