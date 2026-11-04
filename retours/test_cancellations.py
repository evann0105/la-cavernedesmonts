"""The cancellation refund ledger is not a physical return dossier."""
import uuid

from django.test import TestCase, override_settings
from django.urls import reverse

from expeditions.test_cancellations import CancellationFixture
from expeditions.tests import LOGIN_BACKEND, TEST_SETTINGS
from .models import ReturnEvent, ReturnMail, ReturnRequest, ReturnShipment
from .services import (ReturnError, acknowledge_shipments, add_shipment, complete,
                       mark_checked, mark_received, mark_shipped, submit)
from .worklist import open_returns


@override_settings(**TEST_SETTINGS)
class CancellationReturnIsolationTests(CancellationFixture, TestCase):
    def test_cancellation_ledger_cannot_be_processed_as_a_return(self):
        cancellation = self.cancel('succeeded')
        refund = cancellation.refund
        for action in (mark_shipped, mark_received, mark_checked, acknowledge_shipments):
            with self.subTest(action=action.__name__), self.assertRaisesMessage(ReturnError, 'annulée'):
                action(refund.pk, self.seller)
        with self.assertRaisesMessage(ReturnError, 'annulée'):
            complete(refund.pk, self.seller, {}, {})
        with self.assertRaisesMessage(ReturnError, 'annulée'):
            add_shipment(refund.pk, uuid.uuid4(), tracking_number='TEST')
        self.assertFalse(ReturnShipment.objects.exists())
        self.assertFalse(ReturnEvent.objects.exists())
        self.assertFalse(ReturnMail.objects.exists())
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.quantity, 5)

    def test_no_new_return_can_be_submitted_for_a_cancelled_order(self):
        self.cancel('failed')
        with self.assertRaisesMessage(ReturnError, 'annulée'):
            submit(self.order.pk, {0: 1}, uuid.uuid4())
        self.assertEqual(ReturnRequest.objects.count(), 1)
        self.assertFalse(ReturnMail.objects.exists())

    def test_hidden_from_return_queues_and_old_management_link_redirects(self):
        cancellation = self.cancel()
        self.assertFalse(open_returns().exists())
        response = self.client.get(reverse('retours:manage'))
        self.assertEqual(response.context['records'].paginator.count, 0)
        for method in ('get', 'post'):
            response = getattr(self.client, method)(reverse('retours:manage_detail', args=[cancellation.refund_id]),
                                                   {'action': 'receive'})
            self.assertRedirects(response, reverse('annulations:detail', args=[self.order.pk]),
                                 fetch_redirect_response=False)
        self.assertFalse(ReturnEvent.objects.exists())

    def test_customer_return_page_shows_cancellation_without_parcel_instructions(self):
        cancellation = self.cancel()
        self.client.force_login(self.customer, backend=LOGIN_BACKEND)
        response = self.client.get(reverse('retours:order', args=[self.order.pk]))
        self.assertContains(response, 'Commande annulée')
        self.assertContains(response, 'aucun retour n’est nécessaire')
        self.assertNotContains(response, 'Instructions de retour')
        self.assertNotContains(response, 'Confirmer la rétractation')
        response = self.client.get(reverse('retours:mine'))
        self.assertEqual(response.context['records'].paginator.count, 0)
        response = self.client.get(reverse('retours:shipment', args=[cancellation.refund_id]))
        self.assertEqual(response.status_code, 404)

    def test_customer_service_does_not_offer_cancellation_as_a_physical_return(self):
        self.cancel()
        from clients.selectors import get_customer
        from clients.relationships import relationship_options
        customer = get_customer(f'u-{self.customer.pk}')
        options = relationship_options(None, customer=customer)
        self.assertEqual(options['returns'], [])
        response = self.client.get(reverse('clients:profile', args=[f'u-{self.customer.pk}']),
                                   {'onglet': 'returns'})
        self.assertEqual(response.context['page_obj'].paginator.count, 0)
        tabs = {key: count for key, _, count in response.context['tabs']}
        self.assertEqual(tabs['returns'], 0)
        response = self.client.get(reverse('clients:profile', args=[f'u-{self.customer.pk}']),
                                   {'onglet': 'orders'})
        self.assertContains(response, 'Commande annulée')
        self.assertContains(response, reverse('annulations:detail', args=[self.order.pk]))
        self.assertContains(response, 'Remboursement en cours')
