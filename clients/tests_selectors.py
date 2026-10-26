import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.http import Http404
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from avoirs.models import CustomerBenefit
from paiement.models import Order
from .models import SupportCase
from .selectors import find_customers, get_customer, load_directory


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CustomerDirectoryTests(TestCase):
    def user(self, username='customer', email='client@example.test', **extra):
        return get_user_model().objects.create_user(username=username, email=email, **extra)

    def order(self, *, email='client@example.test', user=None, name='Camille Montagne', status='paid', total=8000, **extra):
        values = {'session_key': 'directory-test', 'items': [], 'subtotal': 8000,
                  'customer_email': email, 'user': user, 'status': status,
                  'billing_details': {'name': name, 'phone': '04 50 00 00 00'},
                  'shipping_details': {}, 'total_paid': total,
                  'paid_at': timezone.now() if status == 'paid' else None}
        values.update(extra)
        return Order.objects.create(**values)

    def benefit(self, email, *, user=None, bound_user_id=None):
        return CustomerBenefit.objects.create(recipient_email=email, user=user,
            bound_user_id=bound_user_id, label='Geste commercial', value=Decimal('15.00'))

    def case(self, *, email='client@example.test', name='Camille Montagne', user=None, order=None, phone='06 00 00 00 00', **extra):
        values = {'creation_key': uuid.uuid4(), 'creation_fingerprint': 'a' * 64,
                  'customer_name': name, 'customer_email': email, 'phone': phone,
                  'subject': 'Une demande reçue par téléphone', 'description': 'Rappeler le client.',
                  'user': user, 'order': order}
        values.update(extra)
        return SupportCase.objects.create(**values)

    def test_account_groups_its_orders_codes_and_cases_without_writing(self):
        user = self.user(first_name='Camille', last_name='Montagne')
        order = self.order(user=user)
        benefit = self.benefit(user.email, user=user, bound_user_id=user.pk)
        case = self.case(user=user, order=order)
        with CaptureQueriesContext(connection) as queries:
            rows = load_directory()
        self.assertEqual(len(rows), 1)
        customer = rows[0]
        self.assertEqual(customer.key, f'u-{user.pk}')
        self.assertEqual(customer.name, 'Camille Montagne')
        self.assertEqual(customer.user_id, user.pk)
        self.assertEqual(customer.order_ids, [order.pk])
        self.assertEqual(customer.benefit_ids, [benefit.pk])
        self.assertEqual(customer.case_ids, [case.pk])
        self.assertEqual(customer.open_case_count, 1)
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for query in queries))
        self.assertEqual(len(queries), 4)

    def test_unbound_guests_group_only_by_exact_normalized_email(self):
        first = self.order(email=' Guest@Example.Test ')
        second = self.order(email='guest@example.test')
        benefit = self.benefit('GUEST@example.test')
        case = self.case(email='guest@example.test')
        self.assertEqual(len(load_directory()), 1)
        customer = get_customer(f'o-{first.pk}')
        self.assertCountEqual(customer.order_ids, [first.pk, second.pk])
        self.assertEqual(customer.benefit_ids, [benefit.pk])
        self.assertEqual(customer.case_ids, [case.pk])
        for key in (f'o-{second.pk}', f'b-{benefit.pk}', f'c-{case.pk}'):
            self.assertEqual(get_customer(key).key, customer.key)
        self.assertIsNone(customer.user_id)

    def test_unbound_guest_records_can_join_current_account_without_reassignment(self):
        order = self.order()
        benefit = self.benefit('client@example.test')
        case = self.case()
        original_key = get_customer(f'o-{order.pk}').key
        user = self.user(first_name='Camille', last_name='Montagne')
        customer = get_customer(original_key)
        self.assertEqual(customer.key, f'u-{user.pk}')
        self.assertEqual(customer.user_id, user.pk)
        order.refresh_from_db(); benefit.refresh_from_db(); case.refresh_from_db()
        self.assertIsNone(order.user_id)
        self.assertIsNone(benefit.user_id)
        self.assertIsNone(benefit.bound_user_id)
        self.assertIsNone(case.user_id)

    def test_explicit_account_binding_wins_over_other_accounts_current_email(self):
        first = self.user(username='first', email='old@example.test', first_name='Camille')
        second = self.user(username='second', email='reused@example.test', first_name='Other')
        order = self.order(user=first, email=second.email)
        benefit = self.benefit(second.email, user=first, bound_user_id=first.pk)
        case = self.case(user=first, email=second.email)
        original = get_customer(f'u-{first.pk}')
        unrelated = get_customer(f'u-{second.pk}')
        self.assertEqual(original.order_ids, [order.pk])
        self.assertEqual(original.benefit_ids, [benefit.pk])
        self.assertEqual(original.case_ids, [case.pk])
        self.assertEqual(original.email, first.email)
        self.assertEqual(unrelated.order_ids, [])
        self.assertEqual(unrelated.benefit_ids, [])
        self.assertEqual(unrelated.case_ids, [])

    def test_code_for_deleted_account_stays_separate_from_email_reuse(self):
        old = self.user(username='former', email='reused@example.test')
        old_id = old.pk
        benefit = self.benefit(old.email, user=old, bound_user_id=old_id)
        old.delete()
        new = self.user(username='replacement', email='reused@example.test')
        old_record = get_customer(f'b-{benefit.pk}')
        self.assertIsNone(old_record.user_id)
        self.assertEqual(old_record.benefit_ids, [benefit.pk])
        self.assertEqual(get_customer(f'u-{new.pk}').benefit_ids, [])
        self.assertEqual(len(load_directory()), 2)

    def test_retained_code_binding_joins_original_existing_account_even_without_fk(self):
        original = self.user(username='original', email='now@example.test')
        other = self.user(username='other', email='old@example.test')
        benefit = self.benefit(other.email, bound_user_id=original.pk)
        self.assertEqual(get_customer(f'b-{benefit.pk}').user_id, original.pk)
        self.assertEqual(get_customer(f'u-{other.pk}').benefit_ids, [])

    def test_conflicting_code_bindings_do_not_pick_either_account(self):
        first = self.user(username='first', email='first@example.test')
        second = self.user(username='second', email='second@example.test')
        benefit = self.benefit(first.email, user=first, bound_user_id=second.pk)
        self.assertIsNone(get_customer(f'b-{benefit.pk}').user_id)
        self.assertEqual(get_customer(f'u-{first.pk}').benefit_ids, [])
        self.assertEqual(get_customer(f'u-{second.pk}').benefit_ids, [])

    def test_homonyms_and_empty_emails_do_not_merge_by_name_or_phone(self):
        a = self.order(email='a@example.test', name='Camille Martin')
        b = self.order(email='b@example.test', name='Camille Martin')
        empty_a = self.order(email='', name='Camille Martin')
        empty_b = self.order(email='', name='Camille Martin')
        phone_a = self.case(email='', name='Camille Martin')
        phone_b = self.case(email='', name='Camille Martin')
        rows = load_directory()
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({get_customer(key).key for key in [f'o-{a.pk}', f'o-{b.pk}', f'o-{empty_a.pk}',
                             f'o-{empty_b.pk}', f'c-{phone_a.pk}', f'c-{phone_b.pk}']}), 6)

    def test_case_linked_to_guest_order_is_grouped_by_order_even_if_contact_changes(self):
        order = self.order(email='checkout@example.test')
        case = self.case(email='callback@example.test', order=order)
        self.assertEqual(len(load_directory()), 1)
        self.assertEqual(get_customer(f'c-{case.pk}').order_ids, [order.pk])

    def test_explicit_phone_contact_keeps_followups_together_without_matching_phone(self):
        source = self.case(email='', name='Camille Martin')
        followup = self.case(email='', name='Camille Martin', contact_reference=source.pk)
        with_email = self.case(email='new-contact@example.test', name='Camille Martin', contact_reference=source.pk)
        unrelated = self.case(email='', name='Camille Martin')
        rows = load_directory()
        self.assertEqual(len(rows), 2)
        customer = get_customer(f'c-{source.pk}')
        self.assertEqual(customer.key, f'c-{source.pk}')
        self.assertCountEqual(customer.case_ids, [source.pk, followup.pk, with_email.pk])
        self.assertEqual(customer.email, 'new-contact@example.test')
        self.assertEqual(customer.open_case_count, 3)
        self.assertEqual(get_customer(f'c-{followup.pk}').key, customer.key)
        self.assertEqual(get_customer(f'c-{with_email.pk}').key, customer.key)
        self.assertEqual(get_customer(f'c-{unrelated.pk}').case_ids, [unrelated.pk])
        self.assertEqual([row.key for row in find_customers('new-contact@')], [customer.key])

    def test_explicit_contact_reference_reuses_email_guests_existing_group(self):
        source = self.case(email='guest@example.test')
        other_original = self.case(email='guest@example.test')
        followup = self.case(email='updated@example.test', contact_reference=source.pk)
        customer = get_customer(f'c-{source.pk}')
        self.assertEqual(len(load_directory()), 1)
        self.assertCountEqual(customer.case_ids, [source.pk, other_original.pk, followup.pk])
        self.assertEqual(get_customer(f'c-{followup.pk}').key, customer.key)

    def test_explicit_contact_reference_does_not_give_new_email_account_ownership(self):
        source = self.case(email='')
        user = self.user(email='reused@example.test')
        followup = self.case(email=user.email, contact_reference=source.pk)
        self.assertIsNone(get_customer(f'c-{followup.pk}').user_id)
        self.assertCountEqual(get_customer(f'c-{source.pk}').case_ids, [source.pk, followup.pk])
        self.assertEqual(get_customer(f'u-{user.pk}').case_ids, [])

    def test_account_and_order_binding_win_over_contact_reference(self):
        source = self.case(email='')
        user = self.user()
        order = self.order(email='buyer@example.test')
        account_case = self.case(user=user, email=user.email, contact_reference=source.pk)
        order_case = self.case(order=order, email='buyer@example.test', contact_reference=source.pk)
        self.assertEqual(get_customer(f'c-{source.pk}').case_ids, [source.pk])
        self.assertEqual(get_customer(f'u-{user.pk}').case_ids, [account_case.pk])
        self.assertEqual(get_customer(f'o-{order.pk}').case_ids, [order_case.pk])

    def test_missing_contact_root_keeps_explicit_followups_together(self):
        removed_source = uuid.uuid4()
        first = self.case(email='', contact_reference=removed_source)
        second = self.case(email='new@example.test', contact_reference=removed_source)
        self.assertEqual(len(load_directory()), 1)
        self.assertCountEqual(get_customer(f'c-{first.pk}').case_ids, [first.pk, second.pk])

    def test_search_ignores_accents_case_spacing_and_supports_partial_email(self):
        user = self.user(first_name='Élodie', last_name='MéGret', email='elodie.megret@example.test')
        self.order(user=user, name='Ancien nom de facturation', email='old-mail@example.test',
                   shipping_details={'name': 'Destinataire différent'})
        for query in ('ELODIE', 'megret', '  elodie   megret ', 'megret@exam', 'old-mail', 'ancien facturation', 'destinataire different', 'customer'):
            with self.subTest(query=query):
                self.assertEqual([row.user_id for row in find_customers(query)], [user.pk])
        self.assertEqual(find_customers('personne inconnue'), [])

    def test_paid_totals_exclude_simulations_and_unconfirmed_amounts(self):
        user = self.user()
        self.order(user=user, status='paid', total=7200)
        self.order(user=user, status='paid', total=None)
        self.order(user=user, status='demo', total=5000)
        self.order(user=user, status='pending', total=9999)
        self.order(user=user, status='expired', total=9999)
        customer = get_customer(f'u-{user.pk}')
        self.assertEqual(customer.order_count, 5)
        self.assertEqual(customer.paid_count, 2)
        self.assertEqual(customer.paid_total, Decimal('72.00'))
        self.assertEqual(customer.paid_unknown_count, 1)
        self.assertEqual(customer.demo_count, 1)

    def test_recent_support_activity_sorts_first_and_resolved_cases_are_not_open(self):
        first = self.user(username='first', email='first@example.test')
        second = self.user(username='second', email='second@example.test')
        older = timezone.now() - timedelta(days=10)
        get_user_model().objects.filter(pk__in=[first.pk, second.pk]).update(date_joined=older)
        self.case(user=first, email=first.email, state='resolved', resolved_at=timezone.now())
        self.case(user=second, email=second.email, state='waiting_customer')
        rows = load_directory()
        self.assertEqual(rows[0].user_id, second.pk)
        self.assertEqual(rows[0].open_case_count, 1)
        self.assertEqual(rows[1].open_case_count, 0)

    def test_inactive_accounts_remain_identifiable_without_reactivation(self):
        user = self.user(is_active=False)
        row = get_customer(f'u-{user.pk}')
        self.assertEqual(row.account_label, 'Compte désactivé')
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_missing_malformed_or_pii_keys_raise_not_found(self):
        for key in ('client@example.test', 'u-0', 'u--1', 'u-999999', 'c-invalid', f'o-{uuid.uuid4()}', None, '../admin/'):
            with self.subTest(key=key):
                with self.assertRaises(Http404):
                    get_customer(key)
