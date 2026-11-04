"""A cancellation can never re-enter the parcel preparation pipeline."""
import uuid
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from annulations.models import Cancellation
from paiement.models import Order
from retours.models import ReturnMail, ReturnRequest
from tableau.home import shipment_attention
from .contacts import correct_contacts
from .models import Fulfillment, FulfillmentEvent
from .public import public_status
from .selectors import summary
from .services import FulfillmentError, change_state
from .tests import FulfillmentFixture, LOGIN_BACKEND, TEST_SETTINGS


class CancellationFixture(FulfillmentFixture):
    def cancel(self, refund_state='pending', fulfillment_state='cancelled'):
        refund = ReturnRequest.objects.create(order=self.order, kind='cancellation',
            submission_id=uuid.uuid4(), amount=7800, shipping_amount=600,
            refund_state=refund_state)
        cancellation = Cancellation.objects.create(order=self.order, refund=refund,
            reason='Annulation demandée par le client.', requested_by=self.seller,
            stock_error='PRIVATE_STOCK_ERROR', refund_error='PRIVATE_REFUND_ERROR')
        Fulfillment.objects.update_or_create(order=self.order, defaults={
            'state': fulfillment_state,
            'ready_at': timezone.now() if fulfillment_state == 'ready' else None,
            'shipped_at': None,
        })
        return cancellation


@override_settings(**TEST_SETTINGS)
class CancellationDispatchTests(CancellationFixture, TestCase):
    def test_cancel_button_visible_before_shipping_and_absent_after_departure(self):
        target = reverse('annulations:detail', args=[self.order.pk])
        response = self.client.get(reverse('expeditions:detail', args=[self.order.pk]))
        self.assertContains(response, 'Annuler avant expédition')
        self.assertContains(response, target)
        self.ready()
        self.ship()
        response = self.client.get(reverse('expeditions:detail', args=[self.order.pk]))
        self.assertNotContains(response, target)
        self.assertFalse(response.context['shipment']['can_cancel'])

    def test_all_transitions_blocked_even_after_refund_failure(self):
        self.cancel('failed', fulfillment_state='ready')
        # The cancellation remains authoritative even if an old operational row
        # is still marked ready. No retry may revive it.
        for action in ('ready', 'reopen', 'ship', 'update_tracking', 'resend_notice'):
            with self.subTest(action=action), self.assertRaisesMessage(FulfillmentError, 'annulée'):
                change_state(self.order.pk, self.seller, 1, action)
        with self.assertRaisesMessage(FulfillmentError, 'annulée'):
            correct_contacts(self.order.pk, self.seller, 1, 'email',
                             'corrected@example.test', 'Motif de test', True)
        info = summary(Order.objects.get(pk=self.order.pk))
        self.assertEqual(info['state'], 'cancelled')
        for flag in ('can_ready', 'can_reopen', 'can_ship', 'can_correct_contacts',
                     'can_update_tracking', 'can_resend_notice', 'can_cancel'):
            self.assertFalse(info[flag], flag)
        self.assertFalse(ReturnMail.objects.exists())
        self.assertFalse(FulfillmentEvent.objects.exists())
        self.order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(self.order.status, 'paid')
        self.assertEqual(self.variant.quantity, 5)

    def test_cancelled_queue_is_separate_from_work_to_prepare(self):
        self.cancel(fulfillment_state='ready')
        self.assertEqual(shipment_attention()['total'], 0)
        overview = reverse('expeditions:overview')
        for state in ('preparing', 'ready'):
            response = self.client.get(overview, {'state': state})
            self.assertEqual(response.context['page_obj'].paginator.count, 0)
        response = self.client.get(overview, {'state': 'cancelled'})
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertEqual(response.context['rows'][0]['waiting'], None)
        self.assertContains(response, 'Suivre l’annulation')
        self.assertContains(response, 'Remboursement à suivre')

    def test_existing_preparation_and_contacts_redirect_to_cancellation(self):
        self.cancel()
        destination = reverse('annulations:detail', args=[self.order.pk])
        for name in ('expeditions:detail', 'expeditions:contacts'):
            for method in ('get', 'post'):
                response = getattr(self.client, method)(reverse(name, args=[self.order.pk]))
                self.assertRedirects(response, destination, fetch_redirect_response=False)

    def test_customer_sees_public_reason_and_actual_refund_status_only(self):
        cancellation = self.cancel('pending')
        self.client.force_login(self.customer, backend=LOGIN_BACKEND)
        url = reverse('espace:order', args=[self.order.pk])
        pending = self.client.get(url)
        self.assertContains(pending, 'Commande annulée')
        self.assertContains(pending, cancellation.reason)
        self.assertContains(pending, 'Remboursement en cours de traitement')
        self.assertNotContains(pending, 'Remboursement confirmé')
        self.assertNotContains(pending, 'PRIVATE_')
        self.assertNotContains(pending, 'Déclarer une rétractation')
        cancellation.refund.refund_state = 'succeeded'
        cancellation.refund.save(update_fields=['refund_state'])
        confirmed = self.client.get(url)
        self.assertContains(confirmed, 'Remboursement confirmé')
        self.assertContains(confirmed, '78,00')
        info = public_status(Order.objects.get(pk=self.order.pk))
        self.assertEqual(info['refund_amount'], Decimal('78.00'))
        self.assertFalse(info['tracking_number'])
        self.assertFalse(info['tracking_url'])

    def test_delivery_slip_cannot_be_mistaken_for_active_preparation(self):
        self.cancel()
        response = self.client.get(reverse('facturation:delivery_slip', args=[self.order.pk]))
        self.assertContains(response, 'COMMANDE ANNULÉE')
        self.assertContains(response, 'Ne pas préparer ni expédier')
        self.assertNotContains(response, 'À glisser dans le carton')
        self.assertNotContains(response, 'Préparé par :')
