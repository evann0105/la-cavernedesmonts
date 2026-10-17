from django.test import TestCase
from core.models import Product, Category
from core.catalog_forms import CatalogProductForm


class CatalogFilterTests(TestCase):
    def setUp(self):
        self.women = Category.objects.create(name='Femmes', slug='femmes')
        self.baby = Category.objects.create(name='Bébé', slug='polaires-bebe')
        self.p = Product.objects.create(name='Veste polaire femme', price=30, category=self.women, sizes='M, XL', availability='available')
        self.other = Product.objects.create(name='Veste légère', price=40, category=self.women, sizes='12 mois', availability='unknown')

    def test_filters_combine_exact_sizes_and_type(self):
        response = self.client.get('/produits/', {'categorie':'femmes','type':'polaires','taille':'M','disponibilite':'available'})
        self.assertEqual(list(response.context['products']),[self.p])
        self.assertContains(response, 'Polaires femme')
        self.assertEqual(self.client.get('/produits/',{'taille':'L'}).context['page_obj'].paginator.count,0)
        self.assertEqual(self.client.get('/produits/',{'disponibilite':'unknown'}).context['page_obj'].paginator.count,1)

    def test_override_baby_collections_and_drafts(self):
        self.p.article_type='vestes'; self.p.save()
        self.p.collections.add(self.baby)
        self.assertEqual(self.client.get('/produits/',{'type':'polaires'}).context['page_obj'].paginator.count,0)
        response=self.client.get('/produits/',{'categorie':'cadeaux-bebe'})
        self.assertEqual(list(response.context['products']),[self.p])
        self.p.is_published=False;self.p.save()
        self.assertEqual(self.client.get('/produits/',{'categorie':'cadeaux-bebe'}).context['page_obj'].paginator.count,0)

    def test_unavailable_cannot_be_added(self):
        self.p.availability='unavailable';self.p.save()
        self.client.post(f'/panier/ajouter/{self.p.slug}/',{'size':'M','quantity':1})
        self.assertFalse(self.client.session.get('cart'))
        response=self.client.get(f'/produits/{self.p.slug}/')
        self.assertNotContains(response,'class="product-purchase"')
        form=CatalogProductForm(instance=self.p)
        self.assertIn('availability',form.fields)
        self.assertIn('article_type',form.fields)

    def test_variant_sizes_and_reserved_stock_override_legacy_fields(self):
        from stocks.models import Variant
        Variant.objects.create(product=self.p, size='S', color='Bleu', quantity=2, reserved=2)
        Variant.objects.create(product=self.p, size='L', color='Vert', quantity=3)
        Variant.objects.create(product=self.p, size='XS', color='Gris', quantity=None)
        def selected(**params):
            return list(self.client.get('/produits/', params).context['products'])
        self.assertNotIn(self.p, selected(taille='M'))
        self.assertNotIn(self.p, selected(taille='S', disponibilite='available'))
        self.assertIn(self.p, selected(taille='S', disponibilite='unavailable'))
        self.assertIn(self.p, selected(taille='L', disponibilite='available'))
        self.assertIn(self.p, selected(taille='XS', disponibilite='unknown'))
        self.assertIn(self.p, selected(disponibilite='available'))
        self.p.availability='unavailable'; self.p.save()
        self.assertNotIn(self.p, selected(taille='L', disponibilite='available'))
        self.assertIn(self.p, selected(taille='L', disponibilite='unavailable'))
