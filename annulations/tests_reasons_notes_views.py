"""Public reason suggestions and private notes preserve authored text."""
from html.parser import HTMLParser
import time
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from expeditions.models import Fulfillment
from retours.models import ReturnMail, ReturnRequest
from stocks.models import Reservation

from . import tests_views
from .mail import notice
from .models import Cancellation
from .reasons import REASON_PRESETS, REASON_TEXTS
from .services import preview as real_preview, save_internal_note


@override_settings(
    DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=False,
    EMAIL_DELIVERY_ENABLED=False, EMAIL_TEST_MODE=False,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SITE_URL='http://testserver',
)
class CancellationReasonNoteViewTests(TestCase):
    record = tests_views.CancellationViewTests.record
    confirm_data = tests_views.CancellationViewTests.confirm_data

    def setUp(self):
        tests_views.CancellationViewTests.setUp(self)

    def save_note(self, text, revision=0, **kwargs):
        return self.client.post(self.url, {
            'action': 'save_note', 'internal_note': text, 'revision': revision, **kwargs,
        })

    def prepare_real_preview(self):
        self.order.stripe_session_id = 'cs_cancellation_ui_preview'
        self.order.save(update_fields=['stripe_session_id'])
        Reservation.objects.create(order=self.order, variant=self.variant, quantity=2, state='sold')
        return real_preview(self.order)

    def test_new_confirmation_has_public_presets_and_separate_private_note(self):
        response = self.client.get(self.url)
        for code, label, suggestion in REASON_PRESETS:
            self.assertContains(response, f'value="{code}"')
            self.assertContains(response, label)
            self.assertContains(response, f'data-reason-text="{suggestion}"')
        self.assertContains(response, 'Pour la boutique uniquement')
        self.assertContains(response, 'Appliquer le texte proposé')
        self.assertContains(response, 'annulations/reasons-notes.js')
        self.assertNotContains(response, 'data-cancellation-live')

    def test_confirmation_preserves_custom_public_text_and_sends_private_note_separately(self):
        with patch('annulations.views.services.request_cancellation') as service:
            response = self.client.post(self.url, self.confirm_data(
                reason_code='client_request', reason='Votre demande par téléphone est enregistrée.',
                internal_note='Note réservée : rappel prévu demain.',
            ))
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        service.assert_called_once_with(
            order_id=self.order.pk, actor=self.seller,
            reason='Votre demande par téléphone est enregistrée.', reason_code='client_request',
            internal_note='Note réservée : rappel prévu demain.',
            preview_token='reviewed-preview', confirmed=True,
        )

    def test_suggestion_also_works_without_javascript_when_public_field_is_empty(self):
        with patch('annulations.views.services.request_cancellation') as service:
            response = self.client.post(self.url, self.confirm_data(reason_code='order_error', reason=''))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(service.call_args.kwargs['reason'], REASON_TEXTS['order_error'])

    def test_invalid_confirmation_keeps_authored_reason_and_private_note(self):
        with patch('annulations.views.services.request_cancellation') as service:
            response = self.client.post(self.url, self.confirm_data(
                reason_code='unavailable', reason='Message personnalisé à conserver.',
                internal_note='Brouillon de note interne.', confirmed='',
            ))
        self.assertEqual(response.status_code, 400)
        form = response.context['form']
        self.assertEqual(form['reason'].value(), 'Message personnalisé à conserver.')
        self.assertEqual(form['internal_note'].value(), 'Brouillon de note interne.')
        self.assertEqual(form['reason_code'].value(), 'unavailable')
        service.assert_not_called()

    def test_expired_preview_preserves_draft_and_allows_explicit_reconfirmation(self):
        self.prepare_real_preview()
        with patch('django.core.signing.time.time', return_value=time.time() - 3601):
            expired = real_preview(self.order)['token']
        fresh = real_preview(self.order)
        self.preview_mock.return_value = fresh
        with patch('annulations.services._run_refund') as start_refund:
            response = self.client.post(self.url, self.confirm_data(
                preview_token=expired, reason_code='client_request',
                reason='Le client souhaite garder cet autre modèle.',
                internal_note='Rappel privé prévu à 15 heures.',
            ))
            self.assertEqual(response.status_code, 400)
            form = response.context['form']
            self.assertNotEqual(form['preview_token'].value(), expired)
            self.assertEqual(form['preview_token'].value(), fresh['token'])
            self.assertEqual(form['reason_code'].value(), 'client_request')
            self.assertEqual(form['reason'].value(), 'Le client souhaite garder cet autre modèle.')
            self.assertEqual(form['internal_note'].value(), 'Rappel privé prévu à 15 heures.')
            self.assertFalse(form['confirmed'].value())
            self.assertContains(response, 'cochez à nouveau la confirmation', status_code=400)
            self.assertNotContains(response, 'Actualiser le récapitulatif →', status_code=400)
            for model in (Cancellation, ReturnRequest, ReturnMail, Fulfillment):
                self.assertFalse(model.objects.exists())
            start_refund.assert_not_called()

            # The refreshed POST still needs a new, explicit confirmation.
            unconfirmed = self.client.post(self.url, form.data)
            self.assertEqual(unconfirmed.status_code, 400)
            start_refund.assert_not_called()
            self.assertFalse(Cancellation.objects.exists())
            reviewed = form.data.copy()
            reviewed['confirmed'] = 'on'
            response = self.client.post(self.url, reviewed)
            self.assertRedirects(response, self.url, fetch_redirect_response=False)
            start_refund.assert_called_once()
        cancellation = Cancellation.objects.get(order=self.order)
        self.assertEqual(cancellation.reason, 'Le client souhaite garder cet autre modèle.')
        self.assertEqual(cancellation.reason_code, 'client_request')
        self.assertEqual(cancellation.internal_note, 'Rappel privé prévu à 15 heures.')

    def test_stock_change_renews_preview_without_reusing_confirmation(self):
        previous = self.prepare_real_preview()['token']
        self.variant.quantity += 1
        self.variant.save(update_fields=['quantity'])
        fresh = real_preview(self.order)
        self.preview_mock.return_value = fresh
        with patch('annulations.services._run_refund') as start_refund:
            response = self.client.post(self.url, self.confirm_data(
                preview_token=previous, reason_code='order_error',
                reason='Erreur de taille dans cette commande.', internal_note='Texte privé conservé.',
            ))
        self.assertEqual(response.status_code, 400)
        form = response.context['form']
        self.assertEqual(form['preview_token'].value(), fresh['token'])
        self.assertNotEqual(form['preview_token'].value(), previous)
        self.assertFalse(form['confirmed'].value())
        self.assertEqual(form['reason_code'].value(), 'order_error')
        self.assertEqual(form['reason'].value(), 'Erreur de taille dans cette commande.')
        self.assertEqual(form['internal_note'].value(), 'Texte privé conservé.')
        self.assertFalse(Cancellation.objects.exists())
        self.assertFalse(ReturnRequest.objects.exists())
        start_refund.assert_not_called()

    def test_newly_ineligible_order_keeps_copyable_draft_without_cancel_button(self):
        previous = self.prepare_real_preview()['token']
        sent_at = timezone.now()
        Fulfillment.objects.create(order=self.order, state='shipped', ready_at=sent_at, shipped_at=sent_at)
        self.preview_mock.return_value = real_preview(self.order)
        with patch('annulations.services._run_refund') as start_refund:
            response = self.client.post(self.url, self.confirm_data(
                preview_token=previous, reason_code='unavailable',
                reason='Texte public à conserver.', internal_note='Note privée <ne pas publier>.',
            ))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.context['summary']['eligible'])
        self.assertContains(response, 'Votre saisie est conservée sur cette page', status_code=400)
        self.assertContains(response, 'Motif sélectionné :</strong> Article indisponible', status_code=400)
        self.assertContains(response, '<textarea id="retained-public-reason" rows="4" readonly>Texte public à conserver.</textarea>', status_code=400, html=True)
        self.assertContains(response, '<textarea id="retained-internal-note" rows="4" readonly>Note privée &lt;ne pas publier&gt;.</textarea>', status_code=400, html=True)
        self.assertNotContains(response, 'name="action" value="confirm"', status_code=400)
        self.assertNotContains(response, 'class="button cancellation-confirm"', status_code=400)
        self.assertFalse(Cancellation.objects.exists())
        self.assertFalse(ReturnRequest.objects.exists())
        start_refund.assert_not_called()

    def test_ineligible_order_also_keeps_invalid_fields_and_their_errors(self):
        self.preview_mock.return_value = {**self.preview, 'eligible': False, 'blocked_reason': 'Vérifiez le stock.'}
        with patch('annulations.views.services.request_cancellation') as service:
            response = self.client.post(self.url, self.confirm_data(
                reason='x' * 1001, reason_code='other', internal_note='Brouillon privé malgré erreur.',
            ))
        self.assertEqual(response.status_code, 400)
        self.assertIn('reason', response.context['form'].errors)
        self.assertContains(response, 'x' * 1001, status_code=400)
        self.assertContains(response, 'Brouillon privé malgré erreur.', status_code=400)
        self.assertContains(response, 'au plus 1000 caractères', status_code=400)
        service.assert_not_called()

    def test_save_note_changes_only_private_note_metadata(self):
        cancellation = self.record()
        with patch('annulations.views.services.request_cancellation') as refund, \
                patch('annulations.views.services.reconcile_cancellation') as reconcile, \
                patch('annulations.views.services.retry_stock') as stock, \
                patch('retours.mail.deliver_pending') as email:
            response = self.save_note('Rappeler le client jeudi.')
        self.assertRedirects(response, self.url + '#note-interne', fetch_redirect_response=False)
        cancellation.refresh_from_db()
        self.assertEqual(cancellation.internal_note, 'Rappeler le client jeudi.')
        self.assertEqual(cancellation.internal_note_revision, 1)
        self.assertEqual(cancellation.internal_note_updated_by, self.seller)
        self.assertIsNotNone(cancellation.internal_note_updated_at)
        self.assertEqual(cancellation.reason, 'Annulation demandée par le client.')
        self.assertEqual(cancellation.refund.refund_state, 'pending')
        self.assertIsNone(cancellation.stock_completed_at)
        self.assertFalse(ReturnMail.objects.exists())
        for action in (refund, reconcile, stock, email):
            action.assert_not_called()

    def test_conflict_keeps_draft_and_shows_latest_note_for_explicit_review(self):
        cancellation = self.record()
        save_internal_note(self.order.pk, self.seller, 'Note enregistrée depuis un autre onglet.', 0)
        response = self.save_note('Ma saisie à conserver.', revision=0)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.context['note_conflict'])
        form = response.context['note_form']
        self.assertEqual(form['internal_note'].value(), 'Ma saisie à conserver.')
        self.assertEqual(form['revision'].value(), '0')
        self.assertContains(response, 'Note enregistrée depuis un autre onglet.', status_code=409)
        self.assertContains(response, 'name="reviewed_revision" value="1"', status_code=409)
        self.assertContains(response, 'Enregistrer ma saisie après vérification', status_code=409)
        cancellation.refresh_from_db()
        self.assertEqual(cancellation.internal_note, 'Note enregistrée depuis un autre onglet.')
        response = self.save_note('Ma saisie à conserver.', revision=0, reviewed_revision='1')
        self.assertRedirects(response, self.url + '#note-interne', fetch_redirect_response=False)
        cancellation.refresh_from_db()
        self.assertEqual(cancellation.internal_note, 'Ma saisie à conserver.')
        self.assertEqual(cancellation.internal_note_revision, 2)

    def test_reviewed_revision_still_rejects_a_new_concurrent_change(self):
        cancellation = self.record()
        save_internal_note(self.order.pk, self.seller, 'Première autre version.', 0)
        save_internal_note(self.order.pk, self.seller, 'Nouvelle autre version.', 1)
        response = self.save_note('Brouillon toujours conservé.', revision=0, reviewed_revision=1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.context['note_form']['internal_note'].value(), 'Brouillon toujours conservé.')
        self.assertContains(response, 'Nouvelle autre version.', status_code=409)
        self.assertContains(response, 'name="reviewed_revision" value="2"', status_code=409)
        cancellation.refresh_from_db()
        self.assertEqual(cancellation.internal_note, 'Nouvelle autre version.')

    def test_invalid_note_or_revision_never_changes_saved_note(self):
        cancellation = self.record()
        for text, revision in (('x' * 2001, 0), ('Texte conservé', ''), ('Texte conservé', -1), ('Texte conservé', 2147483648)):
            with self.subTest(revision=revision, length=len(text)):
                response = self.save_note(text, revision=revision)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.context['note_form']['internal_note'].value(), text)
        cancellation.refresh_from_db()
        self.assertEqual(cancellation.internal_note, '')
        self.assertEqual(cancellation.internal_note_revision, 0)

    def test_clearing_note_preserves_public_reason_and_idempotent_save_has_no_new_revision(self):
        cancellation = self.record()
        self.save_note('Note temporaire.')
        self.save_note('', revision=1)
        cancellation.refresh_from_db()
        self.assertEqual(cancellation.internal_note, '')
        self.assertEqual(cancellation.internal_note_revision, 2)
        self.assertEqual(cancellation.reason, 'Annulation demandée par le client.')
        response = self.save_note('', revision=1)
        self.assertEqual(response.status_code, 302)
        cancellation.refresh_from_db()
        self.assertEqual(cancellation.internal_note_revision, 2)

    def test_note_requires_staff_permission_and_csrf(self):
        cancellation = self.record()
        self.client.force_login(self.customer, backend=tests_views.LOGIN_BACKEND)
        self.assertEqual(self.save_note('Interdit').status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.seller, backend=tests_views.LOGIN_BACKEND)
        self.assertEqual(strict.post(self.url, {'action': 'save_note', 'internal_note': 'Interdit', 'revision': 0}).status_code, 403)
        cancellation.refresh_from_db()
        self.assertEqual(cancellation.internal_note, '')

    def test_note_cannot_be_saved_before_cancellation_exists(self):
        with patch('annulations.views.services.save_internal_note') as save:
            response = self.save_note('Note en avance.')
        self.assertEqual(response.status_code, 400)
        save.assert_not_called()

    def test_note_is_not_in_customer_order_or_notifications(self):
        cancellation = self.record()
        save_internal_note(self.order.pk, self.seller, 'PRIVATE_NOTE_MUST_NOT_LEAK_42', 0)
        cancellation.refresh_from_db()
        for kind in ('cancellation_requested', 'cancellation_refund'):
            notice(cancellation, kind)
        for message in ReturnMail.objects.all():
            self.assertNotIn('PRIVATE_NOTE_MUST_NOT_LEAK_42', str(message.payload))
            self.assertIn(cancellation.reason, message.payload['details'])
        self.client.force_login(self.customer, backend=tests_views.LOGIN_BACKEND)
        response = self.client.get(reverse('espace:order', args=[self.order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'PRIVATE_NOTE_MUST_NOT_LEAK_42')
        self.assertNotContains(response, 'Note interne')

    def test_note_form_is_outside_all_live_regions(self):
        cancellation = self.record()
        save_internal_note(self.order.pk, self.seller, 'Texte privé stable.', 0)
        response = self.client.get(self.url)

        class FormRegions(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
                self.note_regions = []
                self.region_names = []

            def handle_starttag(self, tag, attributes):
                attrs = dict(attributes)
                if 'data-live-region' in attrs:
                    self.region_names.append(attrs['data-live-region'])
                if attrs.get('name') == 'internal_note':
                    self.note_regions = [item.get('data-live-region') for _, item in self.stack if 'data-live-region' in item]
                if tag not in {'input', 'meta', 'link', 'br', 'hr', 'img', 'path'}:
                    self.stack.append((tag, attrs))

            def handle_endtag(self, tag):
                for index in range(len(self.stack) - 1, -1, -1):
                    if self.stack[index][0] == tag:
                        del self.stack[index:]
                        break

        parser = FormRegions()
        parser.feed(response.content.decode())
        self.assertEqual(parser.region_names, ['progress', 'stock', 'notifications'])
        self.assertEqual(parser.note_regions, [])
        self.assertContains(response, 'data-note-unsaved="false"')
        self.assertContains(response, 'data-live-total-label')
        self.assertEqual(response.context['note_form']['internal_note'].value(), 'Texte privé stable.')
