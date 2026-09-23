import hashlib
from unittest.mock import patch
from django.test import TestCase
from django.utils.translation import override
from core.benefits import catalog_benefits
from core.catalog_forms import CatalogProductForm
from core.models import Product, Category


class ProductBenefitsTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Femmes', slug='femmes')
        self.product = Product.objects.create(name='Polaire', slug='polaire', price=30, category=self.category, description='Une polaire douce.', static_image='photo.webp')
        self.reviewed = {'polaire': {'description_sha256':hashlib.sha256(self.product.description.encode()).hexdigest(), 'benefits':['soft']}}

    def test_only_reviewed_unchanged_descriptions_receive_default_benefits(self):
        with patch('core.benefits.reviewed_catalog', return_value=self.reviewed), override('fr'):
            self.assertEqual(len(self.product.benefit_lines), 1)
            self.product.description = 'Modèle différent, imperméable et performant.'
            self.assertEqual(self.product.benefit_lines, [])
            self.product.slug = 'unknown'
            self.assertEqual(catalog_benefits(self.product), [])

    def test_custom_copy_translation_and_explicit_removal(self):
        self.product.benefits = 'Premier conseil\nDeuxième conseil'
        self.product.translations = {'en': {'benefits':'English advice'}}
        with override('en'):
            self.assertEqual(self.product.benefit_lines, ['English advice'])
        with override('de'):
            self.assertEqual(len(self.product.benefit_lines), 2)
            self.assertEqual(self.product.benefits_language, 'fr')
        self.product.benefits = ''
        with override('en'):
            self.assertEqual(self.product.benefit_lines, [])

    def test_form_saves_and_validates_benefits(self):
        data = {'name':'Polaire', 'price':'30', 'category':self.category.pk, 'description':'Douce', 'benefits':'  Conseil vérifié  \n\nAutre conseil', 'benefits_en':'Verified advice'}
        form = CatalogProductForm(data, instance=self.product)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.benefits, 'Conseil vérifié\nAutre conseil')
        self.assertEqual(saved.translations['en']['benefits'], 'Verified advice')
        data['benefits'] = 'a\nb\nc\nd'
        form = CatalogProductForm(data, instance=saved)
        self.assertFalse(form.is_valid())
        self.assertIn('benefits', form.errors)
        data['benefits'] = ''
        form = CatalogProductForm(data, instance=saved)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().benefits, '')

    def test_form_prefills_reviewed_copy_in_all_languages(self):
        with patch('core.benefits.reviewed_catalog', return_value=self.reviewed):
            form = CatalogProductForm(instance=self.product)
            self.assertIn('douce', form.initial['benefits'])
            self.assertIn('soft', form['benefits_en'].value())

    def test_render_escapes_copy_and_hides_empty_block(self):
        self.product.benefits = '<script>alert(1)</script>'
        self.product.save()
        response = self.client.get(f'/produits/{self.product.slug}/')
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.product.benefits = ''
        self.product.save()
        self.assertNotContains(self.client.get(f'/produits/{self.product.slug}/'), 'id="benefits-title"')
