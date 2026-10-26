"""SAV data obeys account export boundaries and suspends premature cleanup review."""
import json
import uuid
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from clients.models import SupportCase, SupportEvent
from paiement.models import Order
from .privacy import retention_review, subject_export


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SupportPrivacyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('sav-private-client',
            email='sav-client@example.test', password='PriveSav2026!', first_name='Camille')
        self.other = get_user_model().objects.create_user('sav-private-other',
            email='sav-other@example.test', password='AutreSav2026!')
        self.actor = get_user_model().objects.create_user('NEVER_EXPORT_STAFF_SAV', is_staff=True)

    def order(self, user=None, email='', status='paid'):
        return Order.objects.create(user=user, session_key='NEVER_EXPORT_SESSION',
            customer_email=email, status=status, subtotal=4500, items=[])

    def case(self, *, user=None, order=None, state='open', **values):
        data = dict(user=user, order=order, customer_name='Camille',
            customer_email=self.user.email, phone='0600000000', subject='Demande SAV personnelle',
            description='La manche présente un défaut.', creation_key=uuid.uuid4(),
            creation_fingerprint='a' * 64, created_by=self.actor, updated_by=self.actor,
            state=state, resolved_at=timezone.now() if state == 'resolved' else None)
        data.update(values)
        return SupportCase.objects.create(**data)

    def counts(self, now=None):
        return {entry['code']: entry['nombre'] for entry in retention_review(now)['categories']}

    def test_export_only_explicit_account_or_owned_order_cases_and_no_internal_notes(self):
        order = self.order(self.user, self.user.email)
        foreign_order = self.order(self.other, self.user.email)
        own = self.case(user=self.user)
        via_order = self.case(order=order, subject='Demande liée à ma commande')
        SupportEvent.objects.create(case=own, kind='note', state='open',
            actor=self.actor, actor_label=self.actor.username,
            note='NEVER_EXPORT_INTERNAL_NOTE', changes={'private': 'NEVER_EXPORT_INTERNAL_CHANGE'})
        hidden = [
            self.case(subject='NEVER_EXPORT_EMAIL_MATCH_ONLY'),
            self.case(user=self.other, subject='NEVER_EXPORT_OTHER_USER'),
            self.case(user=self.other, order=order, subject='NEVER_EXPORT_CONFLICTING_OWNER'),
            self.case(user=self.user, order=foreign_order, subject='NEVER_EXPORT_FOREIGN_ORDER'),
        ]
        before = (SupportCase.objects.count(), SupportEvent.objects.count())
        data = subject_export(self.user)
        self.assertCountEqual([row['reference'] for row in data['dossiers_sav']],
                              [own.reference, via_order.reference])
        exported = next(row for row in data['dossiers_sav'] if row['reference'] == own.reference)
        self.assertEqual(exported['nom'], 'Camille')
        self.assertEqual(exported['demande'], own.description)
        self.assertEqual(exported['canal'], 'Téléphone')
        self.assertEqual(exported['statut'], 'À traiter')
        self.assertIsNone(exported['commande'])
        self.assertEqual(set(exported), {'reference', 'commande', 'nom', 'email', 'telephone',
            'objet', 'demande', 'canal', 'type', 'statut', 'cree_le', 'modifie_le', 'resolu_le'})
        for case in hidden:
            self.assertNotIn(case.reference, str(data))
        for private in ('NEVER_EXPORT_', 'creation_key', 'creation_fingerprint',
                        'customer_email_hash', 'actor_label'):
            self.assertNotIn(private, str(data))
        self.assertIn('contactez la boutique', data['dossiers_sav_perimetre'])
        self.assertEqual(before, (SupportCase.objects.count(), SupportEvent.objects.count()))

    def test_email_change_keeps_explicit_case_but_does_not_claim_guest_cases(self):
        own = self.case(user=self.user)
        guest = self.case(subject='Demande invitée non attribuée')
        self.user.email = 'new-sav-client@example.test'
        self.user.save(update_fields=['email'])
        data = subject_export(self.user)
        self.assertEqual([row['reference'] for row in data['dossiers_sav']], [own.reference])
        self.assertEqual(data['dossiers_sav'][0]['email'], 'sav-client@example.test')
        self.assertNotIn(guest.reference, str(data))

    def test_reused_email_does_not_reveal_deleted_accounts_case(self):
        old = get_user_model().objects.create_user('deleted-sav-account', email='reused-sav@example.test')
        own = self.case(user=old, customer_email=old.email)
        old.delete()
        new = get_user_model().objects.create_user('replacement-sav-account', email='reused-sav@example.test')
        self.assertEqual(subject_export(new)['dossiers_sav'], [])
        own.refresh_from_db()
        self.assertIsNone(own.user_id)
        self.assertEqual(own.description, 'La manche présente un défaut.')

    def test_password_confirmed_download_announces_and_contains_sav_requests(self):
        own = self.case(user=self.user)
        self.client.force_login(self.user)
        page = self.client.get(reverse('espace:privacy_account'))
        self.assertContains(page, 'demandes SAV')
        response = self.client.post(reverse('espace:privacy_export'), {'current_password': 'PriveSav2026!'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        data = json.loads(response.content)
        self.assertEqual([row['reference'] for row in data['dossiers_sav']], [own.reference])

    def test_open_or_recent_support_case_excludes_account_from_inactivity_review(self):
        now = timezone.now()
        old = now - timedelta(days=1100)
        get_user_model().objects.filter(pk=self.user.pk).update(date_joined=old, last_login=old)
        self.assertEqual(self.counts(now)['inactive_accounts_review'], 1)
        record = self.case(user=self.user)
        SupportCase.objects.filter(pk=record.pk).update(created_at=old, updated_at=old)
        self.assertEqual(self.counts(now)['inactive_accounts_review'], 0)
        SupportCase.objects.filter(pk=record.pk).update(state='resolved', resolved_at=old)
        self.assertEqual(self.counts(now)['inactive_accounts_review'], 1)
        SupportCase.objects.filter(pk=record.pk).update(updated_at=now)
        self.assertEqual(self.counts(now)['inactive_accounts_review'], 0)

    def test_owned_order_support_case_counts_as_activity_even_without_case_user(self):
        now = timezone.now()
        old = now - timedelta(days=1100)
        get_user_model().objects.filter(pk=self.user.pk).update(date_joined=old, last_login=old)
        order = self.order(self.user, self.user.email)
        Order.objects.filter(pk=order.pk).update(created_at=old)
        record = self.case(order=order)
        SupportCase.objects.filter(pk=record.pk).update(created_at=old, updated_at=old)
        self.assertEqual(self.counts(now)['inactive_accounts_review'], 0)
        SupportCase.objects.filter(pk=record.pk).update(state='resolved', resolved_at=old)
        self.assertEqual(self.counts(now)['inactive_accounts_review'], 1)
        SupportCase.objects.filter(pk=record.pk).update(updated_at=now)
        self.assertEqual(self.counts(now)['inactive_accounts_review'], 0)

    def test_open_case_suspends_order_review_for_pending_demo_and_old_paid_orders(self):
        now = timezone.now()
        old = now - timedelta(days=4000)
        orders = [self.order(status=status) for status in ('pending', 'demo', 'paid')]
        Order.objects.filter(pk__in=[order.pk for order in orders]).update(created_at=old)
        keys = ('abandoned_orders_review', 'demo_orders_review', 'old_paid_orders_review')
        self.assertEqual([self.counts(now)[key] for key in keys], [1, 1, 1])
        cases = [self.case(order=order) for order in orders]
        SupportCase.objects.filter(pk__in=[case.pk for case in cases]).update(created_at=old, updated_at=old)
        self.assertEqual([self.counts(now)[key] for key in keys], [0, 0, 0])
        SupportCase.objects.filter(pk__in=[case.pk for case in cases]).update(state='resolved', resolved_at=old)
        self.assertEqual([self.counts(now)[key] for key in keys], [1, 1, 1])

    def test_closed_cases_review_requires_old_resolution_and_no_recent_activity(self):
        now = timezone.now()
        old = now - timedelta(days=100)
        eligible = self.case(state='resolved')
        old_but_updated = self.case(state='resolved')
        recent_resolution = self.case(state='resolved')
        opened = self.case()
        SupportCase.objects.filter(pk__in=[eligible.pk, old_but_updated.pk]).update(resolved_at=old)
        SupportCase.objects.filter(pk__in=[eligible.pk, recent_resolution.pk, opened.pk]).update(updated_at=old)
        self.assertEqual(self.counts(now)['closed_support_cases_review'], 1)
        self.assertEqual(SupportCase.objects.count(), 4)

    def test_retention_report_exposes_counts_only_and_never_deletes_support_records(self):
        old = timezone.now() - timedelta(days=100)
        record = self.case(state='resolved', customer_name='PERSONAL_NAME_NOT_IN_AUDIT',
            subject='PERSONAL_SUBJECT_NOT_IN_AUDIT', description='PERSONAL_DESCRIPTION_NOT_IN_AUDIT')
        SupportCase.objects.filter(pk=record.pk).update(updated_at=old, resolved_at=old)
        event = SupportEvent.objects.create(case=record, actor=self.actor, actor_label=self.actor.username,
            state='resolved', kind='note', note='INTERNAL_NOTE_NOT_IN_AUDIT')
        before = (SupportCase.objects.count(), SupportEvent.objects.count())
        output = StringIO()
        call_command('privacy_audit', '--json', stdout=output)
        data = json.loads(output.getvalue())
        self.assertEqual(data['modifications'], 0)
        self.assertEqual(data['mode'], 'lecture seule')
        self.assertEqual(next(row['nombre'] for row in data['categories']
                              if row['code'] == 'closed_support_cases_review'), 1)
        self.assertEqual(before, (SupportCase.objects.count(), SupportEvent.objects.count()))
        for private in (str(record.pk), record.reference, record.customer_name, record.subject,
                        record.description, record.customer_email, record.phone, event.note, self.actor.username):
            self.assertNotIn(private, output.getvalue())
