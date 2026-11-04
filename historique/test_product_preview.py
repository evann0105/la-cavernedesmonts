from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from connexion.test_helpers import verify_test_user
from core.models import Product
from .products import record_product


class ProductPreviewTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory(); self.addCleanup(folder.cleanup)
        settings = override_settings(MEDIA_ROOT=folder.name)
        settings.enable(); self.addCleanup(settings.disable)
        self.user = get_user_model().objects.create_user('preview-history', is_superuser=True, is_staff=True)
        verify_test_user(self.user); self.client.force_login(self.user)
        self.product = Product.objects.create(name='Ancienne polaire', price='80.00',
            description='Description conservée', information_review_notes='NOTE STRICTEMENT PRIVÉE',
            is_published=False, benefits=None)
        with patch('core.benefits.catalog_benefits', return_value=['Bénéfice ancien vérifié']), transaction.atomic():
            self.version = record_product(self.product, actor=self.user)

    def test_real_storefront_template_preserves_resolved_copy_and_never_shows_private_notes(self):
        with patch('core.benefits.catalog_benefits', return_value=['Bénéfice recalculé à ignorer']), CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('historique:frame', args=[self.version.pk]))
        self.assertContains(response, 'product-detail')
        self.assertContains(response, 'Bénéfice ancien vérifié')
        self.assertNotContains(response, 'Bénéfice recalculé à ignorer')
        self.assertNotContains(response, 'NOTE STRICTEMENT PRIVÉE')
        self.assertNotContains(response, 'data-price-endpoint=')
        self.assertContains(response, '<fieldset disabled class="history-preview-purchase">')
        self.assertFalse([query for query in queries if query['sql'].lstrip().startswith(('INSERT', 'UPDATE', 'DELETE'))])

    def test_missing_primary_photo_does_not_hide_the_retained_text(self):
        self.product.main_image.save('photo.webp', ContentFile(b'ancienne image'))
        with transaction.atomic():
            version = record_product(self.product, actor=self.user)
        self.product.main_image.delete(save=False)
        for asset in version.assets:
            Path(self.product.main_image.storage.path(asset['archive'])).unlink()
        response = self.client.get(reverse('historique:frame', args=[version.pk]))
        self.assertContains(response, 'Description conservée')
        self.assertContains(response, 'Photo à venir')
