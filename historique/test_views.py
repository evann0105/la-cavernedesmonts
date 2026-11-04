from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from core.models import Product
from .exceptions import HistoryConflict, HistoryError
from .models import HistoryVersion
from .presentation import product_changes
from .products import snapshot_product
from .services import record


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], EMAIL_DELIVERY_ENABLED=False)
class HistoryViewsTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = override_settings(MEDIA_ROOT=self.directory.name)
        settings.enable(); self.addCleanup(settings.disable)
        self.actor = get_user_model().objects.create_user('responsable-historique', is_staff=True)
        self.actor.user_permissions.add(Permission.objects.get(codename='manage_catalog'))
        verify_test_user(self.actor)
        self.client.force_login(self.actor)
        self.product = Product.objects.create(name='Polaire témoin', slug='polaire-temoin', price=Decimal('80.00'))
        self.snapshot = snapshot_product(self.product)
        self.first = record('product', self.product.pk, self.snapshot, actor=self.actor,
                            object_label=self.product.name, source='baseline')
        self.changed = deepcopy(self.snapshot)
        self.changed['fields']['description'] = 'Description suivante <script>alert(1)</script>'
        self.latest = record('product', self.product.pk, self.changed, actor=self.actor,
                             object_label=self.product.name, label='Description mise à jour')

    def url(self, name, version=None):
        return reverse('historique:' + name, args=[(version or self.first).pk])

    def prepared(self, token='new-signed-token'):
        return {'token': token, 'current_snapshot': self.changed, 'warnings': [],
                'target_label': self.product.name, 'object_url': reverse('core:catalog_edit', args=[self.product.pk])}

    def test_routes_use_one_management_prefix(self):
        self.assertEqual(reverse('historique:index'), '/gestion/historique/')
        self.assertEqual(reverse('historique:homepage'), '/gestion/historique/accueil/')

    def test_all_screens_require_catalog_permission_and_login(self):
        urls = [reverse('historique:index'), reverse('historique:homepage'),
                reverse('historique:product', args=[self.product.pk]),
                *[self.url(name) for name in ('detail', 'preview', 'frame', 'restore')]]
        self.client.logout()
        for url in urls:
            with self.subTest(url=url): self.assertEqual(self.client.get(url).status_code, 302)
        user = get_user_model().objects.create_user('client-curieux', is_staff=True)
        verify_test_user(user); self.client.force_login(user)
        for url in urls:
            with self.subTest(url=url): self.assertEqual(self.client.get(url).status_code, 403)

    def test_reading_lists_and_review_never_calls_restoration(self):
        count = HistoryVersion.objects.count()
        with patch('historique.views.services.apply_restore') as apply, patch('historique.views.services.prepare_restore', return_value=self.prepared()):
            for url in [reverse('historique:index'), reverse('historique:product', args=[self.product.pk]), self.url('detail'), self.url('preview'), self.url('restore')]:
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, url)
                self.assertIn('no-store', response['Cache-Control'])
                self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
            apply.assert_not_called()
        self.assertEqual(HistoryVersion.objects.count(), count)

    def test_filters_are_combined_and_scoped_product_cannot_escape_scope(self):
        other = record('product', 999999, {'fields': {'name': 'Autre fiche'}}, object_label='Autre fiche')
        response = self.client.get(reverse('historique:index'), {'q': 'polaire', 'source': 'edit', 'actor': self.actor.username,
            'since': timezone.localdate().isoformat(), 'until': timezone.localdate().isoformat()})
        self.assertEqual([v.pk for v in response.context['page_obj']], [self.latest.pk])
        response = self.client.get(reverse('historique:product', args=[self.product.pk]), {'scope': 'homepage'})
        self.assertEqual({v.pk for v in response.context['page_obj']}, {self.first.pk, self.latest.pk})
        self.assertNotIn(other.pk, [v.pk for v in response.context['page_obj']])

    def test_invalid_filter_range_explains_without_showing_unfiltered_records(self):
        response = self.client.get(reverse('historique:index'), {'since': '2026-12-30', 'until': '2026-01-01'})
        self.assertContains(response, 'La date de fin doit être après la date de début.')
        self.assertEqual(response.context['page_obj'].paginator.count, 0)

    def test_pagination_retains_search_and_actor(self):
        for position in range(25):
            record('product', self.product.pk, {'fields': {'name': f'Polaire {position}'}}, actor=self.actor, object_label='Polaire témoin')
        response = self.client.get(reverse('historique:index'), {'q': 'Polaire', 'actor': self.actor.username})
        self.assertEqual(len(response.context['page_obj']), 20)
        self.assertContains(response, 'page=2')
        self.assertContains(response, 'q=Polaire&amp;actor=responsable-historique')

    def test_difference_uses_previous_version_and_escapes_text(self):
        response = self.client.get(self.url('detail', self.latest))
        self.assertEqual(response.context['previous'].pk, self.first.pk)
        self.assertContains(response, 'Description suivante &lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        baseline = self.client.get(self.url('detail'))
        self.assertContains(baseline, 'Aucune version antérieure')
        self.assertTrue(baseline.context['baseline'])

    def test_frozen_actor_and_label_survive_deletion(self):
        self.actor.delete()
        replacement = get_user_model().objects.create_user('autre-responsable', is_superuser=True, is_staff=True)
        verify_test_user(replacement); self.client.force_login(replacement)
        response = self.client.get(self.url('detail', self.latest))
        self.assertContains(response, 'responsable-historique')
        self.assertContains(response, 'Polaire témoin')

    def test_missing_product_keeps_history_without_dead_editor_link(self):
        pk = self.product.pk
        self.product.delete()
        response = self.client.get(reverse('historique:product', args=[pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['object_url'])
        response = self.client.get(self.url('restore'))
        self.assertContains(response, 'Cette fiche a été supprimée')
        self.assertNotContains(response, 'name="confirmed"')

    def test_out_of_range_identifiers_return_not_found(self):
        for name in ('detail', 'preview', 'frame', 'restore', 'product'):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse('historique:' + name, args=[10**30])).status_code, 404)

    def test_preview_sandbox_no_forms_or_scripts_granted(self):
        response = self.client.get(self.url('preview'))
        self.assertContains(response, 'sandbox="allow-same-origin"')
        self.assertNotContains(response, 'allow-scripts')
        self.assertNotContains(response, 'allow-forms')
        self.assertContains(response, 'Ordinateur')
        self.assertContains(response, 'Téléphone')

    def test_frame_is_same_origin_private_and_sandboxed_even_when_opened_directly(self):
        with patch('historique.products.preview_response', return_value=HttpResponse('<p>Aperçu</p>')) as preview:
            response = self.client.get(self.url('frame'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.assertIn("script-src 'none'", response['Content-Security-Policy'])
        self.assertIn("form-action 'none'", response['Content-Security-Policy'])
        self.assertTrue(preview.call_args.args[0]._history_preview)

    def test_unavailable_preview_remains_private_and_explained(self):
        with patch('historique.products.preview_response', side_effect=HistoryError('La photo manque.')):
            response = self.client.get(self.url('frame'))
        self.assertContains(response, 'La photo manque.', status_code=409)
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')

    def test_confirmation_is_required_and_financial_or_stock_mutations_not_triggered(self):
        with patch('historique.views.services.prepare_restore', return_value=self.prepared()), patch('historique.views.services.apply_restore') as apply:
            response = self.client.post(self.url('restore'), {'confirmation': 'old-token'})
            self.assertContains(response, 'Cochez la confirmation', status_code=400)
            self.assertEqual(response.context['form']['confirmation'].value(), 'new-signed-token')
            apply.assert_not_called()

    def test_valid_confirmation_passes_target_id_and_redirects_to_new_entry(self):
        with patch('historique.views.services.apply_restore', return_value=self.latest) as apply:
            response = self.client.post(self.url('restore'), {'confirmation': 'signed-token', 'confirmed': 'on'})
        apply.assert_called_once_with(self.actor, 'signed-token', expected_entry_id=self.first.pk)
        self.assertRedirects(response, self.url('detail', self.latest), fetch_redirect_response=False)

    def test_stale_review_refreshes_comparison_and_clears_confirmation(self):
        with patch('historique.views.services.apply_restore', side_effect=HistoryConflict('Le contenu a changé.')) as apply, patch('historique.views.services.prepare_restore', return_value=self.prepared()):
            response = self.client.post(self.url('restore'), {'confirmation': 'expired', 'confirmed': 'on'})
        self.assertContains(response, 'La comparaison ci-dessous a été actualisée.', status_code=409)
        self.assertEqual(response.context['form']['confirmation'].value(), 'new-signed-token')
        self.assertFalse(response.context['form']['confirmed'].value())
        self.assertEqual(apply.call_count, 1)

    def test_missing_photos_block_confirmation_without_hiding_preview(self):
        with patch('historique.views.services.prepare_restore', side_effect=HistoryError('La photo conservée est introuvable.')):
            response = self.client.get(self.url('restore'))
        self.assertContains(response, 'La photo conservée est introuvable.')
        self.assertContains(response, self.url('preview'))
        self.assertNotContains(response, 'name="confirmed"')

    def test_post_requires_csrf_even_for_manager(self):
        client = Client(enforce_csrf_checks=True); client.force_login(self.actor)
        with patch('historique.views.services.apply_restore') as apply:
            response = client.post(self.url('restore'), {'confirmation': 'signed-token', 'confirmed': 'on'})
        self.assertEqual(response.status_code, 403)
        apply.assert_not_called()

    def test_removed_imported_photo_is_compared_and_price_is_explicit(self):
        before = deepcopy(self.snapshot); after = deepcopy(self.snapshot)
        before['imported_gallery'] = [{'kind': 'static', 'name': 'old.webp', 'alt': 'Avant'}]
        after['imported_gallery'] = []
        after['fields']['price'] = '75.50'
        with patch('historique.presentation.image_url', return_value='/media/valid.webp'):
            rows = [row for section in product_changes(before, after) for row in section['rows']]
        photo = next(row for row in rows if row['kind'] == 'image')
        self.assertEqual(photo['before']['url'], '/media/valid.webp')
        self.assertFalse(photo['after']['url'])
        self.assertEqual(next(row for row in rows if row['label'] == 'Prix général')['after'], '75,50 €')

    def test_historical_image_uses_archive_when_original_disappeared(self):
        original = Path(self.directory.name) / 'products/ancienne.webp'
        original.parent.mkdir(); original.write_bytes(b'photographie')
        snapshot = deepcopy(self.snapshot); snapshot['fields']['main_image'] = 'products/ancienne.webp'
        version = record('product', self.product.pk, snapshot, actor=self.actor, object_label=self.product.name,
                         assets=[{'kind': 'media', 'name': 'products/ancienne.webp'}])
        original.unlink()
        response = self.client.get(self.url('detail', version))
        self.assertContains(response, version.assets[0]['archive'])
        self.assertNotContains(response, 'Photo conservée introuvable')

    def test_current_version_has_honest_badge_and_editor_instead_of_restore_action(self):
        response = self.client.get(self.url('detail'))
        self.assertContains(response, 'Correspond au contenu actuel')
        self.assertContains(response, 'Modifier le contenu actuel')
        self.assertNotContains(response, self.url('restore'))
        response = self.client.get(self.url('detail', self.latest))
        self.assertNotContains(response, 'Correspond au contenu actuel')
        self.assertContains(response, self.url('restore', self.latest))

    def test_homepage_baseline_is_described_with_no_previous_snapshot(self):
        from .homepage import snapshot_homepage
        snapshot = snapshot_homepage()
        version = record('homepage', 1, snapshot, object_label='Accueil', source='baseline')
        response = self.client.get(self.url('detail', version))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Le contenu conservé')
        self.assertNotContains(response, 'Comparaison indisponible')

    def test_missing_token_does_not_keep_checked_confirmation_after_refresh(self):
        with patch('historique.views.services.prepare_restore', return_value=self.prepared()), patch('historique.views.services.apply_restore') as apply:
            response = self.client.post(self.url('restore'), {'confirmed': 'on'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.context['form']['confirmed'].value())
        self.assertEqual(response.context['form']['confirmation'].value(), 'new-signed-token')
        apply.assert_not_called()
