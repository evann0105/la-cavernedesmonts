"""The seller's confirmation screen must never initiate money movement on GET."""
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from core.models import Product
from expeditions.models import Fulfillment
from paiement.models import Order
from retours.models import ReturnLine, ReturnRequest
from stocks.models import Variant

from .models import Cancellation
from .services import CancellationError


LOGIN_BACKEND = 'connexion.backends.EmailOrUsernameBackend'


@override_settings(
    DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=False,
    EMAIL_DELIVERY_ENABLED=False, EMAIL_TEST_MODE=False,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SITE_URL='http://testserver',
)
class CancellationViewTests(TestCase):
    def setUp(self):
        self.seller = get_user_model().objects.create_user(
            'cancel-ui-seller', email='seller@example.test', is_staff=True,
        )
        self.seller.user_permissions.add(Permission.objects.get(
            content_type__app_label='retours', codename='manage_returns',
        ))
        self.customer = get_user_model().objects.create_user(
            'cancel-ui-customer', email='customer@example.test',
        )
        verify_test_user(self.seller)
        verify_test_user(self.customer)
        self.product = Product.objects.create(name='Polaire test', slug='cancel-ui-polaire', price=Decimal('40'))
        self.variant = Variant.objects.create(product=self.product, size='M', color='Vert', quantity=3)
        self.order = Order.objects.create(
            user=self.customer, session_key='cancel-ui-order', status='paid',
            subtotal=8000, total_paid=6800, paid_line_totals=[6000], return_shipping_amount=800,
            customer_email='customer@example.test', paid_at=timezone.now(),
            items=[{'product': self.product.pk, 'variant': self.variant.pk,
                    'name': 'Polaire commandée', 'size': 'M', 'color': 'Vert',
                    'quantity': 2, 'unit_amount': 4000}],
        )
        self.url = reverse('annulations:detail', args=[self.order.pk])
        self.preview = {
            'eligible': True, 'blocked_reason': '', 'token': 'reviewed-preview',
            'amount': Decimal('68'), 'shipping_amount': Decimal('8'),
            'merchandise_amount': Decimal('60'),
            'lines': [{'name': 'Polaire commandée', 'size': 'M', 'color': 'Vert',
                       'quantity': 2, 'amount': Decimal('60'), 'stock_label': 'M · Vert'}],
        }
        self.client.force_login(self.seller, backend=LOGIN_BACKEND)
        self.preview_patch = patch('annulations.views.services.preview', return_value=self.preview)
        self.preview_mock = self.preview_patch.start()
        self.addCleanup(self.preview_patch.stop)

    def record(self, state='pending', stock_completed=False, **kwargs):
        refund = ReturnRequest.objects.create(
            order=self.order, submission_id=uuid.uuid4(), kind='cancellation',
            amount=6800, shipping_amount=800, refund_state=state,
        )
        ReturnLine.objects.create(
            request=refund, item_index=0, variant=self.variant,
            name='Polaire commandée', size='M', color='Vert',
            quantity=2, amount=6000, unit_amount=4000,
        )
        Fulfillment.objects.create(order=self.order, state='cancelled')
        return Cancellation.objects.create(
            order=self.order, refund=refund, requested_by=self.seller,
            reason='Annulation demandée par le client.',
            stock_completed_at=timezone.now() if stock_completed else None, **kwargs,
        )

    def confirm_data(self, **kwargs):
        return {'action': 'confirm', 'reason': 'Le client a changé d’avis.',
                'confirmed': 'on', 'preview_token': 'reviewed-preview', **kwargs}

    def test_route_and_get_are_private_read_only(self):
        self.assertEqual(self.url, f'/gestion/expeditions/{self.order.pk}/annuler/')
        with patch('annulations.views.services.request_cancellation') as request_refund, \
                patch('annulations.views.services.reconcile_cancellation') as reconcile, \
                patch('annulations.views.services.retry_stock') as restock:
            response = self.client.get(self.url, HTTP_REFERER='http://testserver/gestion/aide/')
        self.assertContains(response, 'Annuler et rembourser 68,00 €')
        self.assertContains(response, 'Articles après réduction')
        self.assertContains(response, '60,00 €')
        self.assertContains(response, 'Quantité : 2')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
        self.assertEqual(response.context['back_url'], reverse('expeditions:detail', args=[self.order.pk]))
        self.assertFalse(response.context['back_use_history'])
        self.assertFalse(Cancellation.objects.exists())
        self.assertFalse(ReturnRequest.objects.exists())
        for function in (request_refund, reconcile, restock):
            function.assert_not_called()

    def test_customer_and_staff_without_permission_cannot_use_route(self):
        with patch('annulations.views.services.request_cancellation') as request_refund:
            self.client.force_login(self.customer, backend=LOGIN_BACKEND)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.post(self.url, self.confirm_data()).status_code, 403)
            self.customer.is_staff = True
            self.customer.save(update_fields=['is_staff'])
            self.assertEqual(self.client.get(self.url).status_code, 403)
        request_refund.assert_not_called()

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=', response.url)
        self.preview_mock.assert_not_called()

    def test_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.seller, backend=LOGIN_BACKEND)
        with patch('annulations.views.services.request_cancellation') as request_refund:
            self.assertEqual(client.post(self.url, self.confirm_data()).status_code, 403)
        request_refund.assert_not_called()

    def test_reason_confirmation_and_preview_are_required(self):
        for field in ('reason', 'confirmed', 'preview_token'):
            with self.subTest(field=field), patch('annulations.views.services.request_cancellation') as request_refund:
                response = self.client.post(self.url, self.confirm_data(**{field: ''}))
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.context['form'].errors)
                request_refund.assert_not_called()

    def test_confirmation_sends_only_cleaned_fields_to_service(self):
        with patch('annulations.views.services.request_cancellation') as request_refund:
            response = self.client.post(self.url, self.confirm_data(reason='  Demande du client.  '))
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        request_refund.assert_called_once_with(
            order_id=self.order.pk, actor=self.seller, reason='Demande du client.',
            reason_code='', internal_note='', preview_token='reviewed-preview', confirmed=True,
        )

    def test_service_rejection_keeps_reason_and_refreshes_review_without_navigation(self):
        with patch('annulations.views.services.request_cancellation', side_effect=CancellationError('Le récapitulatif a expiré.')):
            response = self.client.post(self.url, self.confirm_data())
        self.assertContains(response, 'Le récapitulatif a expiré.', status_code=400)
        self.assertContains(response, 'Le récapitulatif a été actualisé', status_code=400)
        self.assertNotContains(response, 'Actualiser le récapitulatif →', status_code=400)
        self.assertEqual(response.context['form']['reason'].value(), 'Le client a changé d’avis.')
        self.assertFalse(response.context['form']['confirmed'].value())

    def test_committed_request_is_reloaded_after_service_error(self):
        def committed_then_error(**kwargs):
            self.record()
            raise CancellationError('Confirmation bancaire indisponible.')
        with patch('annulations.views.services.request_cancellation', side_effect=committed_then_error):
            response = self.client.post(self.url, self.confirm_data())
        self.assertContains(response, 'Annulation enregistrée', status_code=400)
        self.assertNotContains(response, 'name="action" value="confirm"', status_code=400)
        self.assertContains(response, 'Vérifier le remboursement', status_code=400)
        self.preview_mock.assert_not_called()

    def test_pending_record_uses_saved_amounts_without_new_preview(self):
        self.record(refund_error='Le prestataire est indisponible.')
        with patch('annulations.views.services.reconcile_cancellation') as reconcile:
            response = self.client.get(self.url)
        self.assertContains(response, '68,00 €')
        self.assertContains(response, 'Annulation demandée par le client.')
        self.assertContains(response, 'Le prestataire est indisponible.')
        self.assertContains(response, 'Remise en stock en attente')
        self.assertContains(response, 'Vérifier le remboursement')
        self.assertNotContains(response, 'name="action" value="restock"')
        self.preview_mock.assert_not_called()
        reconcile.assert_not_called()

    def test_existing_confirmation_does_not_repeat_service(self):
        self.record()
        with patch('annulations.views.services.request_cancellation') as request_refund:
            response = self.client.post(self.url, self.confirm_data())
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        request_refund.assert_not_called()

    def test_stock_retry_is_only_present_after_confirmed_refund(self):
        self.record(state='succeeded', stock_error='Identité du stock à vérifier.')
        response = self.client.get(self.url)
        self.assertContains(response, 'Remise en stock à vérifier')
        self.assertContains(response, 'Identité du stock à vérifier.')
        self.assertContains(response, 'Réessayer la remise en stock')
        self.assertNotContains(response, 'name="action" value="reconcile"')

    def test_completed_record_has_no_financial_or_stock_action(self):
        self.record(state='succeeded', stock_completed=True)
        response = self.client.get(self.url)
        self.assertContains(response, 'Articles remis en stock')
        self.assertContains(response, 'Total remboursé')
        for action in ('confirm', 'reconcile', 'restock'):
            self.assertNotContains(response, f'name="action" value="{action}"')

    def test_recorded_cancellation_back_links_go_to_list_without_redirect_loop(self):
        self.record()
        response = self.client.get(self.url, HTTP_REFERER=f'http://testserver{self.url}')
        expected = reverse('expeditions:overview') + '?state=cancelled'
        self.assertEqual(response.context['return_url'], expected)
        self.assertEqual(response.context['back_url'], expected)
        self.assertFalse(response.context['back_use_history'])
        self.assertNotContains(response, f'href="{reverse("expeditions:detail", args=[self.order.pk])}"')
        self.assertContains(response, 'Voir les commandes annulées')

    def test_completed_demo_has_simulation_labels_and_returns_to_demo_list(self):
        self.order.status = 'demo'
        self.order.save(update_fields=['status'])
        self.record(state='succeeded', stock_completed=True)
        response = self.client.get(self.url)
        self.assertEqual(response.context['back_url'], reverse('expeditions:overview') + '?state=cancelled&scope=demo')
        self.assertContains(response, '<h3>Stock simulé</h3>', html=True)
        self.assertContains(response, '<span data-live-total-label>Total simulé</span>', html=True)
        self.assertContains(response, 'Aucun stock réel modifié.')
        self.assertNotContains(response, '<h3>Articles remis en stock</h3>', html=True)
        self.assertNotContains(response, '<span data-live-total-label>Total remboursé</span>', html=True)

    def test_reconcile_and_stock_retry_use_dedicated_services(self):
        self.record()
        for action, service in (('reconcile', 'reconcile_cancellation'), ('restock', 'retry_stock')):
            with self.subTest(action=action), patch(f'annulations.views.services.{service}') as function, \
                    patch('annulations.views.services.request_cancellation') as request_refund:
                response = self.client.post(self.url, {'action': action})
                self.assertRedirects(response, self.url, fetch_redirect_response=False)
                function.assert_called_once_with(order_id=self.order.pk, actor=self.seller)
                request_refund.assert_not_called()

    def test_unknown_action_and_retry_without_cancellation_are_rejected(self):
        for action in ('unknown', 'reconcile', 'restock'):
            with self.subTest(action=action), patch('annulations.views.services.reconcile_cancellation') as reconcile, \
                    patch('annulations.views.services.retry_stock') as restock:
                response = self.client.post(self.url, {'action': action})
                self.assertEqual(response.status_code, 400)
                reconcile.assert_not_called()
                restock.assert_not_called()

    def test_blocked_preview_never_offers_empty_amount_or_confirmation(self):
        self.preview_mock.return_value = {
            'eligible': False, 'blocked_reason': 'Cette commande est déjà expédiée.',
            'token': '', 'amount': None, 'shipping_amount': None,
            'merchandise_amount': None, 'lines': [],
        }
        response = self.client.get(self.url)
        self.assertContains(response, 'Cette commande est déjà expédiée.')
        self.assertNotContains(response, 'name="action" value="confirm"')
        self.assertNotContains(response, 'Total à rembourser')

    def test_demo_is_explicit_and_not_available_in_production(self):
        self.order.status = 'demo'
        self.order.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertContains(response, 'Commande de démonstration')
        self.assertContains(response, 'Simuler l’annulation et le remboursement de 68,00 €')
        with override_settings(DEBUG=False):
            self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_unpaid_order_is_not_available(self):
        self.order.status = 'pending'
        self.order.save(update_fields=['status'])
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.preview_mock.assert_not_called()
