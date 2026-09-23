from pathlib import Path
from django.conf import settings
from django.test import TestCase
from core.models import Product, Category
from core.product_images import image_manifest, thumbnail_url
from core.templatetags.product_images import product_image


class PerformanceTests(TestCase):
    def test_pagination_preserves_filters_sort_and_all_products(self):
        category=Category.objects.create(name='Femmes',slug='femmes')
        for number in range(29):
            Product.objects.create(name=f'Polaire {number}',slug=f'polaire-{number}',category=category,price=number+1)
        Product.objects.create(name='Polaire cachée',price=1,category=category,is_published=False)
        response=self.client.get('/produits/',{'categorie':'femmes','q':'Polaire','tri':'-prix'})
        page=response.context['page_obj']
        self.assertEqual(page.paginator.count,29)
        self.assertEqual(len(page),24)
        self.assertIn('categorie=femmes',response.context['pagination_query'])
        self.assertIn('tri=-prix',response.context['pagination_query'])
        second=self.client.get('/produits/',{'categorie':'femmes','q':'Polaire','tri':'-prix','page':2})
        self.assertEqual(len(second.context['products']),5)
        self.assertFalse(set(p.pk for p in page)&set(p.pk for p in second.context['products']))
        self.assertEqual([p.price for p in page],sorted([p.price for p in page],reverse=True))
        self.assertEqual(self.client.get('/produits/?page=invalid').status_code,200)

    def test_generated_assets_exist_and_uploads_keep_their_original_url(self):
        root=Path(settings.BASE_DIR)/'core/static'
        for source,variants in image_manifest().items():
            self.assertTrue((root/source).is_file())
            self.assertEqual(len({v['width'] for v in variants}),len(variants))
            for v in variants:
                self.assertTrue((root/v['path']).is_file())
        product=Product(name='Photo',price=1,main_image='products/custom.webp')
        self.assertEqual(product_image(product)['src'],product.image_url)
        self.assertEqual(product_image(product)['srcset'],'')
        self.assertEqual(thumbnail_url('/media/products/custom.webp'),'/media/products/custom.webp')

    def test_cards_offer_responsive_images_and_hero_has_mobile_source(self):
        source=next(iter(image_manifest()))
        product=Product.objects.create(name='Photo',price=1,static_image=source)
        response=self.client.get('/produits/')
        self.assertContains(response,'srcset=')
        self.assertContains(response,'loading="eager"')
        self.assertContains(self.client.get('/'),'montagne-mobile-600.webp')
