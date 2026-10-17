import json
from io import StringIO
from pathlib import Path
from django.conf import settings
from django.test import TestCase
from django.core.management import call_command
from core.models import Product
from stocks.models import Variant

class ProductInformationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.data=json.loads((Path(settings.BASE_DIR)/'core/product_information_seed.json').read_text())
        for item in cls.data['products']:
            Product.objects.create(slug=item['slug'],name=item['slug'],source_url=item['source_url'],price=42,description='Description marchande conservée')
        cls.product=Product.objects.get(slug='homme-1067-veste-polaire-hybride-aulp-milor-homme')
        Variant.objects.create(product=cls.product,size='M',color='Gris',quantity=7,reserved=2)

    def apply(self):
        call_command('enrich_product_information',stdout=StringIO())

    def test_reviewed_data_and_inventory_are_preserved(self):
        self.apply()
        self.assertEqual(Product.objects.exclude(brand_contact='').count(),23)
        self.assertEqual(Product.objects.filter(composition__contains='100 %').count(),9)
        self.assertEqual(Product.objects.exclude(information_review_notes='').count(),26)
        for product in Product.objects.all():
            self.assertEqual(product.price,42)
            self.assertEqual(product.description,'Description marchande conservée')
        stock=self.product.stock_variants.get()
        self.assertEqual((stock.quantity,stock.reserved),(7,2))
        self.product.refresh_from_db()
        before=self.product.information_sources
        self.apply();self.product.refresh_from_db()
        self.assertEqual(self.product.information_sources,before)

    def test_partial_compositions_never_invent_a_percentage(self):
        self.apply()
        for slug in ['bebe-101-echarpe-bebe','enfant-75-polaire-garcon-ceven','enfant-76-polaire-garcon-ceven']:
            product=Product.objects.get(slug=slug)
            self.assertNotIn('%',product.composition)
            self.assertIn('sur la fiche d’origine',product.composition)
        test=Product.objects.get(slug='test')
        self.assertEqual((test.composition,test.brand,test.brand_contact),('','',''))
        hood=Product.objects.get(slug='homme-1069-veste-polaire-hybride-aulp-milor-homme')
        self.assertEqual(hood.composition,'')
        self.assertIn('sans capuche',hood.information_review_notes)

    def test_merchant_edits_and_deletions_survive_reimport(self):
        self.apply();self.product.refresh_from_db()
        self.product.composition='Valeur corrigée sur l’étiquette du lot'
        self.product.brand_contact=''
        self.product.information_review_notes=''
        self.product.save()
        self.apply();self.product.refresh_from_db()
        self.assertEqual(self.product.composition,'Valeur corrigée sur l’étiquette du lot')
        self.assertEqual(self.product.brand_contact,'')
        self.assertEqual(self.product.information_review_notes,'')

    def test_edited_fact_does_not_keep_an_unrelated_source_or_date(self):
        source = {'url': 'https://example.test/composition', 'label': 'Fiche fournisseur'}
        product = Product(composition='100 % coton.', information_sources={
            'composition': {
                'value': '100 % coton.', 'sources': [source], 'checked_on': '2026-10-16',
            },
        })
        self.assertEqual(product.information_source_links, [source])
        self.assertEqual(product.information_checked_on, '2026-10-16')
        for new_value in ('Autre composition vérifiée sur étiquette.', ''):
            with self.subTest(new_value=new_value):
                product.composition = new_value
                self.assertEqual(product.information_source_links, [])
                self.assertEqual(product.information_checked_on, '')

    def test_conflicting_product_identity_is_skipped(self):
        self.product.source_url='https://example.test/autre-reference'
        self.product.save()
        self.apply();self.product.refresh_from_db()
        self.assertEqual(self.product.composition,'')
        self.assertEqual(self.product.information_sources,{})

    def test_public_page_shows_sources_and_hides_internal_notes(self):
        self.apply();self.product.refresh_from_db()
        self.product.information_review_notes='PRIVATE_RESEARCH_NOTE'
        self.product.save()
        response=self.client.get('/produits/'+self.product.slug+'/')
        self.assertContains(response,'100 % polyester')
        self.assertContains(response,'AMATEIS')
        self.assertContains(response,'https://www.aulp.fr/products/milor')
        self.assertContains(response,'Contact du responsable de la marque')
        self.assertNotContains(response,'PRIVATE_RESEARCH_NOTE')
        partial=Product.objects.get(slug='enfant-75-polaire-garcon-ceven')
        self.assertContains(self.client.get('/produits/'+partial.slug+'/'),'proportion non précisée')
