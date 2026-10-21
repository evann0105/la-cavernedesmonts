from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from config.encrypted_fields import email_digest
from connexion.test_helpers import verify_test_user
from paiement.models import Order
from .mail import deliver_pending, enqueue
from .models import BenefitEvent, BenefitMail, BenefitRedemption, CustomerBenefit
from .services import BenefitError, consume_locked, notify_benefit, quote, release_locked, reserve_locked, save_benefit


class BenefitFixtures:
    def setUp(self):
        users = get_user_model().objects
        self.staff = users.create_user('seller', email='seller@example.test', is_staff=True)
        self.client_user = users.create_user('beneficiary', email='client@example.test')
        verify_test_user(self.client_user)
        self.other = users.create_user('other', email='other@example.test')
        verify_test_user(self.other)

    def benefit(self, **changes):
        fields = {'recipient_email': self.client_user.email, 'label': 'Merci pour votre fidélité',
                  'kind': 'percent', 'value': Decimal('15.00'), 'minimum_subtotal': Decimal('70.00')}
        fields.update(changes)
        return save_benefit(CustomerBenefit(**fields), self.staff, notify=False)

    def order(self, subtotal=8000, user=None):
        return Order.objects.create(user=user or self.client_user, session_key='domain-test', items=[], subtotal=subtotal,
                                    reservation_expires_at=timezone.now() + timedelta(minutes=35))

    def paid_session(self, order, amount=1200):
        return {'id': 'cs_benefit_test', 'status': 'complete', 'payment_status': 'paid', 'currency': 'eur',
                'metadata': {'order_id': str(order.pk)}, 'client_reference_id': str(order.pk),
                'amount_subtotal': order.subtotal, 'amount_total': order.subtotal - amount,
                'total_details': {'amount_discount': amount, 'amount_shipping': 0}}


@override_settings(EMAIL_DELIVERY_ENABLED=False)
class BenefitDomainTests(BenefitFixtures, TestCase):
    def test_creation_normalizes_and_links_email_and_generates_unique_codes(self):
        first = self.benefit(recipient_email=' CLIENT@EXAMPLE.TEST ')
        second = self.benefit()
        self.assertNotEqual(first.code, second.code)
        self.assertEqual(first.recipient_email, self.client_user.email)
        self.assertEqual(first.recipient_email_hash, email_digest(self.client_user.email))
        self.assertEqual(first.user, self.client_user)
        self.assertEqual(first.bound_user_id, self.client_user.pk)
        self.assertEqual(first.created_by, self.staff)
        self.assertTrue(first.code.startswith('CDM-'))
        self.assertEqual(first.events.count(), 1)

    def test_unknown_recipient_does_not_require_an_existing_account(self):
        benefit = self.benefit(recipient_email='future@example.test')
        self.assertIsNone(benefit.user_id)
        future = get_user_model().objects.create_user('future', email='future@example.test')
        verify_test_user(future)
        order = self.order(user=future)
        with transaction.atomic():
            reserve_locked(order, benefit, future)
        benefit.refresh_from_db()
        self.assertEqual(benefit.user, future)
        self.assertEqual(benefit.bound_user_id, future.pk)

    def test_percentage_and_threshold_and_half_up_rounding(self):
        benefit = self.benefit()
        with self.assertRaises(BenefitError):
            quote(benefit, self.client_user, 6999)
        self.assertEqual(quote(benefit, self.client_user, 7000)['discount_amount'], 1050)
        benefit = self.benefit(value=Decimal('50'), minimum_subtotal=0)
        self.assertEqual(quote(benefit, self.client_user, 101)['discount_amount'], 51)

    def test_fixed_credit_preserves_entire_value_by_requiring_sufficient_basket(self):
        benefit = self.benefit(kind='credit', value=Decimal('80'), minimum_subtotal=Decimal('20'))
        self.assertEqual(benefit.effective_minimum, Decimal('80'))
        with self.assertRaises(BenefitError):
            quote(benefit, self.client_user, 7999)
        self.assertEqual(quote(benefit, self.client_user, 8000)['discount_amount'], 8000)

    def test_amount_coupon_caps_at_merchandise_subtotal(self):
        benefit = self.benefit(kind='amount', value=Decimal('80'), minimum_subtotal=0)
        self.assertEqual(quote(benefit, self.client_user, 2000)['discount_amount'], 2000)

    def test_unverified_customer_cannot_use_when_either_local_bypass_flag_is_disabled(self):
        benefit = self.benefit()
        from connexion.models import EmailVerification
        EmailVerification.objects.filter(user=self.client_user).delete()
        for flags in ({'DEBUG': False, 'LOCAL_SKIP_EMAIL_VERIFICATION': True},
                      {'DEBUG': True, 'LOCAL_SKIP_EMAIL_VERIFICATION': False}):
            with self.subTest(flags=flags), override_settings(**flags), self.assertRaises(BenefitError):
                quote(benefit, self.client_user, 8000)
        self.assertFalse(benefit.redemptions.exists())
        self.assertFalse(EmailVerification.objects.filter(user=self.client_user).exists())

    def test_other_verified_customer_cannot_use_code(self):
        with self.assertRaises(BenefitError):
            quote(self.benefit(), self.other, 8000)

    def test_reassigned_email_does_not_transfer_an_existing_account_benefit(self):
        benefit = self.benefit()
        original_email = self.client_user.email
        self.client_user.email = 'changed@example.test'
        self.client_user.save(update_fields=['email'])
        next_owner = get_user_model().objects.create_user('new-owner', email=original_email)
        verify_test_user(next_owner)
        with self.assertRaises(BenefitError):
            quote(benefit, next_owner, 8000)
        benefit.label = 'Libellé corrigé'
        with self.assertRaises(ValidationError):
            save_benefit(benefit, self.staff, notify=False)
        benefit.refresh_from_db()
        self.assertEqual(benefit.user_id, self.client_user.pk)

    def test_creator_can_explicitly_correct_recipient_address(self):
        benefit = self.benefit()
        benefit.recipient_email = self.other.email
        save_benefit(benefit, self.staff, notify=False)
        benefit.refresh_from_db()
        self.assertEqual(benefit.user_id, self.other.pk)
        self.assertEqual(quote(benefit, self.other, 8000)['discount_amount'], 1200)
        with self.assertRaises(BenefitError):
            quote(benefit, self.client_user, 8000)

    def test_deleted_account_does_not_turn_grant_back_into_an_unclaimed_email_code(self):
        benefit = self.benefit()
        original_id, original_email = self.client_user.pk, self.client_user.email
        self.client_user.delete()
        next_owner = get_user_model().objects.create_user('reused-address', email=original_email)
        verify_test_user(next_owner)
        benefit.refresh_from_db()
        self.assertIsNone(benefit.user_id)
        self.assertEqual(benefit.bound_user_id, original_id)
        with self.assertRaises(BenefitError):
            quote(benefit, next_owner, 8000)
        benefit.label = 'Libellé corrigé sans réattribution'
        save_benefit(benefit, self.staff, notify=False)
        benefit.refresh_from_db()
        self.assertEqual(benefit.bound_user_id, original_id)
        with self.assertRaises(BenefitError):
            quote(benefit, next_owner, 8000)
        save_benefit(benefit, self.staff, notify=False, reassign=True)
        benefit.refresh_from_db()
        self.assertEqual(benefit.user_id, next_owner.pk)
        self.assertEqual(benefit.bound_user_id, next_owner.pk)
        self.assertEqual(quote(benefit, next_owner, 8000)['discount_amount'], 1200)
        self.assertTrue(benefit.events.filter(label='Destinataire réattribué explicitement par la boutique.').exists())

    def test_date_boundaries_start_inclusive_end_exclusive(self):
        now = timezone.now()
        benefit = self.benefit(starts_at=now, ends_at=now + timedelta(hours=1))
        with self.assertRaises(BenefitError):
            quote(benefit, self.client_user, 8000, at=now - timedelta(microseconds=1))
        self.assertEqual(quote(benefit, self.client_user, 8000, at=now)['discount_amount'], 1200)
        with self.assertRaises(BenefitError):
            quote(benefit, self.client_user, 8000, at=benefit.ends_at)

    def test_inactive_code_is_rejected(self):
        with self.assertRaises(BenefitError):
            quote(self.benefit(active=False), self.client_user, 8000)

    def test_invalid_model_values_and_dates_are_rejected(self):
        cases = [{'value': Decimal('0')}, {'value': Decimal('-1')}, {'value': Decimal('100.01')},
                 {'minimum_subtotal': Decimal('-1')}, {'ends_at': timezone.now(), 'starts_at': timezone.now() + timedelta(days=1)}]
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                self.benefit(**fields)

    def test_never_reuses_existing_generated_code_on_collision(self):
        existing = self.benefit()
        candidate = CustomerBenefit(code=existing.code, recipient_email=self.client_user.email,
                                    label='Deuxième', kind='amount', value=10)
        with patch('avoirs.services.generate_code', return_value='CDM-ABCDE-FGHJK'):
            created = save_benefit(candidate, self.staff, notify=False)
        self.assertEqual(created.code, 'CDM-ABCDE-FGHJK')
        self.assertEqual(CustomerBenefit.objects.count(), 2)

    def test_emitted_code_cannot_be_changed_or_deleted(self):
        benefit = self.benefit()
        benefit.code = 'MANUAL-CHANGE'
        with self.assertRaises((ValidationError, BenefitError)):
            save_benefit(benefit, self.staff, notify=False)
        with self.assertRaises(ValidationError):
            benefit.delete()
        with self.assertRaises(ValidationError):
            CustomerBenefit.objects.filter(pk=benefit.pk).delete()

    def test_reserved_code_is_not_consumed_until_confirmed_payment(self):
        benefit = self.benefit()
        order = self.order()
        with transaction.atomic():
            redemption = reserve_locked(order, benefit, self.client_user)
            self.assertEqual(redemption.state, 'held')
            with self.assertRaises(BenefitError):
                consume_locked(order, {**self.paid_session(order), 'payment_status': 'unpaid'})
        redemption.refresh_from_db()
        self.assertEqual(redemption.state, 'held')
        self.assertEqual(benefit.status, 'reserved')

    def test_same_checkout_reservation_is_idempotent(self):
        benefit = self.benefit()
        order = self.order()
        with transaction.atomic():
            first = reserve_locked(order, benefit, self.client_user)
            second = reserve_locked(order, benefit, self.client_user)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(BenefitRedemption.objects.count(), 1)

    def test_unchanged_save_does_not_modify_revision_of_held_benefit(self):
        benefit = self.benefit()
        order = self.order()
        revision, event_count = benefit.updated_at, benefit.events.count()
        with transaction.atomic():
            redemption = reserve_locked(order, benefit, self.client_user)
        event_count += 1
        saved = save_benefit(benefit, self.staff, notify=False)
        benefit.refresh_from_db()
        self.assertEqual(saved.updated_at, revision)
        self.assertEqual(benefit.updated_at, revision)
        self.assertEqual(benefit.events.count(), event_count)
        self.assertEqual(redemption.snapshot['benefit_revision'], revision.isoformat())

    def test_cannot_hold_one_code_in_two_orders(self):
        benefit = self.benefit()
        with transaction.atomic():
            reserve_locked(self.order(), benefit, self.client_user)
            with self.assertRaises(BenefitError):
                reserve_locked(self.order(), benefit, self.client_user)

    def test_db_constraint_prevents_two_live_redemptions(self):
        benefit = self.benefit()
        BenefitRedemption.objects.create(benefit=benefit, order=self.order(), discount_amount=100)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BenefitRedemption.objects.create(benefit=benefit, order=self.order(), discount_amount=100)

    def test_cancelled_checkout_releases_code_for_new_order(self):
        benefit = self.benefit()
        order = self.order()
        with transaction.atomic():
            reserve_locked(order, benefit, self.client_user)
            release_locked(order)
            release_locked(order)
            second = reserve_locked(self.order(), benefit, self.client_user)
        self.assertEqual(second.state, 'held')
        self.assertEqual(benefit.redemptions.filter(state='released').count(), 1)

    def test_confirmed_payment_consumes_single_use_code_once(self):
        benefit = self.benefit()
        order = self.order()
        with transaction.atomic():
            reserve_locked(order, benefit, self.client_user)
            used = consume_locked(order, self.paid_session(order))
            order.status = 'paid'
            order.save(update_fields=['status'])
            self.assertEqual(consume_locked(order, self.paid_session(order)).pk, used.pk)
            release_locked(order)
            with self.assertRaises(BenefitError):
                reserve_locked(self.order(), benefit, self.client_user)
        self.assertEqual(benefit.status, 'used')
        self.assertEqual(benefit.redemptions.get().state, 'used')

    def test_payment_mismatches_leave_code_held(self):
        benefit = self.benefit()
        order = self.order()
        with transaction.atomic():
            reserve_locked(order, benefit, self.client_user)
            for change in [{'total_details': {'amount_discount': 1199}}, {'currency': 'usd'},
                           {'amount_subtotal': 7999}, {'client_reference_id': 'another'},
                           {'metadata': {'order_id': 'another'}}]:
                with self.subTest(change=change), self.assertRaises(BenefitError):
                    consume_locked(order, {**self.paid_session(order), **change})
        self.assertEqual(benefit.redemptions.get().state, 'held')

    def test_valid_reservation_survives_offer_end_during_payment(self):
        benefit = self.benefit(ends_at=timezone.now() + timedelta(minutes=1))
        order = self.order()
        with transaction.atomic():
            reserve_locked(order, benefit, self.client_user)
            with patch('avoirs.services.timezone.now', return_value=timezone.now() + timedelta(minutes=2)):
                self.assertEqual(reserve_locked(order, benefit, self.client_user).discount_amount, 1200)
                consume_locked(order, self.paid_session(order))
        self.assertEqual(benefit.redemptions.get().state, 'used')

    def test_used_and_held_benefits_cannot_be_edited(self):
        benefit = self.benefit()
        order = self.order()
        with transaction.atomic():
            reserve_locked(order, benefit, self.client_user)
        benefit.value = Decimal('16')
        with self.assertRaises(BenefitError):
            save_benefit(benefit, self.staff, notify=False)
        with transaction.atomic():
            consume_locked(order, self.paid_session(order))
        benefit.active = False
        with self.assertRaises(BenefitError):
            save_benefit(benefit, self.staff, notify=False)

    def test_stale_admin_form_does_not_overwrite_newer_changes(self):
        benefit = self.benefit()
        stale = CustomerBenefit.objects.get(pk=benefit.pk)
        benefit.label = 'Nouvelle condition'
        save_benefit(benefit, self.staff, notify=False)
        stale.value = Decimal('20')
        with self.assertRaises(BenefitError):
            save_benefit(stale, self.staff, notify=False)

    def test_unused_benefit_may_be_edited_and_deactivated(self):
        benefit = self.benefit()
        benefit.value = Decimal('20')
        benefit.active = False
        save_benefit(benefit, self.staff, notify=False)
        benefit.refresh_from_db()
        self.assertEqual(benefit.value, Decimal('20'))
        self.assertFalse(benefit.active)

    def test_repeated_creation_with_same_form_nonce_issues_only_one_code(self):
        import uuid
        key = uuid.uuid4()
        fields = dict(recipient_email=self.client_user.email, label='Idempotent', kind='credit', value=20)
        first = save_benefit(CustomerBenefit(**fields), self.staff, notify=False, creation_key=key)
        second = save_benefit(CustomerBenefit(**fields), self.staff, notify=False, creation_key=key)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(CustomerBenefit.objects.count(), 1)

    def test_status_uses_prefetched_redemptions_without_extra_query(self):
        benefit = self.benefit()
        prefetched = CustomerBenefit.objects.prefetch_related('redemptions').get(pk=benefit.pk)
        with self.assertNumQueries(0):
            self.assertEqual(prefetched.status, 'available')

    def test_personal_data_is_encrypted_in_database(self):
        benefit = self.benefit()
        queued = enqueue(benefit)
        with connection.cursor() as cursor:
            cursor.execute('SELECT recipient_email FROM avoirs_customerbenefit WHERE id=%s', [benefit.pk])
            encrypted_email = cursor.fetchone()[0]
            cursor.execute('SELECT payload FROM avoirs_benefitmail WHERE id=%s', [queued.pk])
            encrypted_payload = cursor.fetchone()[0]
        self.assertTrue(encrypted_email.startswith('enc:v1:'))
        self.assertNotIn(self.client_user.email, encrypted_email)
        self.assertTrue(encrypted_payload.startswith('enc:v1:'))
        self.assertNotIn(self.client_user.email, encrypted_payload)


@override_settings(EMAIL_DELIVERY_ENABLED=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', EMAIL_TEST_MODE=True)
class BenefitNotificationTests(BenefitFixtures, TestCase):
    def test_creation_queues_then_delivers_only_after_commit(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            benefit = save_benefit(CustomerBenefit(recipient_email=self.client_user.email, label='Votre avoir', kind='credit', value=20), self.staff)
        self.assertEqual(BenefitMail.objects.filter(benefit=benefit).count(), 1)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 0)
        callbacks[0]()
        benefit.refresh_from_db()
        self.assertIsNotNone(benefit.notified_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.client_user.email])
        self.assertIn(benefit.code, mail.outbox[0].body)
        self.assertIn('/avoirs/', mail.outbox[0].body)
        self.assertIn('une seule fois', mail.outbox[0].body)
        self.assertTrue(mail.outbox[0].subject.startswith('[TEST]'))
        self.assertEqual(deliver_pending(), 0)

    def test_delivery_failure_is_durable_and_does_not_mark_notified(self):
        benefit = self.benefit()
        queued = enqueue(benefit)
        with patch('avoirs.mail.EmailMultiAlternatives.send', side_effect=RuntimeError('private secret')):
            self.assertEqual(deliver_pending(), 0)
        queued.refresh_from_db()
        benefit.refresh_from_db()
        self.assertEqual(queued.attempts, 1)
        self.assertIsNone(queued.sent_at)
        self.assertIsNone(benefit.notified_at)
        self.assertEqual(deliver_pending(), 0)
        BenefitMail.objects.filter(pk=queued.pk).update(last_attempt_at=timezone.now() - timedelta(minutes=6))
        self.assertEqual(deliver_pending(), 1)

    def test_old_notification_is_cancelled_after_recipient_or_conditions_change(self):
        benefit = self.benefit()
        queued = enqueue(benefit)
        benefit.recipient_email = self.other.email
        save_benefit(benefit, self.staff, notify=False)
        queued.refresh_from_db()
        self.assertIsNotNone(queued.cancelled_at)
        self.assertEqual(deliver_pending(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_explicit_resend_is_idempotent_with_same_form_key(self):
        benefit = self.benefit()
        first = notify_benefit(benefit, self.staff, key='single-submit')
        second = notify_benefit(benefit, self.staff, key='single-submit')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(deliver_pending(), 1)
        self.assertEqual(deliver_pending(), 0)

    def test_inactive_benefit_is_not_sent(self):
        benefit = self.benefit(active=False)
        with self.assertRaises(BenefitError):
            notify_benefit(benefit, self.staff)

    def test_saving_inactive_or_expired_code_cannot_promise_notification(self):
        for fields in [{'active': False}, {'ends_at': timezone.now() - timedelta(seconds=1)}]:
            with self.subTest(fields=fields), self.assertRaises(BenefitError):
                save_benefit(CustomerBenefit(recipient_email=self.client_user.email, label='Ancien code', kind='credit', value=20, **fields), self.staff, notify=True)
        self.assertEqual(CustomerBenefit.objects.count(), 0)

    def test_editing_conditions_resets_notification_status_until_new_delivery(self):
        benefit = self.benefit()
        enqueue(benefit)
        self.assertEqual(deliver_pending(), 1)
        benefit.refresh_from_db()
        self.assertIsNotNone(benefit.notified_at)
        benefit.value = Decimal('25')
        save_benefit(benefit, self.staff, notify=False)
        benefit.refresh_from_db()
        self.assertIsNone(benefit.notified_at)

    def test_unchanged_save_preserves_failed_notification_revision_and_history(self):
        benefit = self.benefit()
        queued = enqueue(benefit)
        with patch('avoirs.mail.EmailMultiAlternatives.send', side_effect=RuntimeError('SMTP failure')):
            self.assertEqual(deliver_pending(), 0)
        revision = benefit.updated_at
        event_count = benefit.events.count()
        saved = save_benefit(benefit, self.staff, notify=False)
        benefit.refresh_from_db()
        queued.refresh_from_db()
        self.assertEqual(saved.updated_at, revision)
        self.assertEqual(benefit.updated_at, revision)
        self.assertIsNone(queued.cancelled_at)
        self.assertIsNone(queued.sent_at)
        self.assertEqual(benefit.events.count(), event_count)
        BenefitMail.objects.filter(pk=queued.pk).update(last_attempt_at=timezone.now() - timedelta(minutes=6))
        self.assertEqual(deliver_pending(), 1)
        self.assertEqual(mail.outbox[0].to, [self.client_user.email])

    def test_unchanged_save_with_notify_reuses_current_revision_without_change_event(self):
        benefit = self.benefit()
        revision = benefit.updated_at
        event_count = benefit.events.count()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            first = save_benefit(benefit, self.staff, notify=True)
            second = save_benefit(benefit, self.staff, notify=True)
        benefit.refresh_from_db()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(benefit.updated_at, revision)
        self.assertEqual(benefit.events.count(), event_count)
        self.assertEqual(benefit.notifications.count(), 1)
        self.assertEqual(len(callbacks), 2)
        for callback in callbacks:
            callback()
        self.assertEqual(len(mail.outbox), 1)
