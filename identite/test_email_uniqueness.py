from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from connexion.forms import SignupForm
from .models import User


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
class EmailUniquenessTests(TransactionTestCase):
    def data(self, username='unique-user'):
        return dict(username=username,email='same@example.test',password1='AuditPassword456!x',password2='AuditPassword456!x')

    def test_two_prevalidated_forms_cannot_create_duplicate_email(self):
        first=SignupForm(self.data('first'));second=SignupForm(self.data('second'))
        self.assertTrue(first.is_valid());self.assertTrue(second.is_valid())
        first.save()
        with self.assertRaises(IntegrityError), transaction.atomic():second.save()
        self.assertEqual(User.objects.filter(email__iexact='SAME@example.test').count(),1)

    def test_parallel_signups_store_exactly_one_account(self):
        if connection.vendor != 'postgresql':self.skipTest('PostgreSQL concurrency test')
        barrier=Barrier(2)
        def signup(name):
            close_old_connections()
            try:
                form=SignupForm(self.data(name))
                assert form.is_valid(), form.errors
                barrier.wait(timeout=10)
                try:
                    with transaction.atomic():form.save()
                    return True
                except IntegrityError:return False
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(signup,['parallel-one','parallel-two']))
        self.assertEqual(sorted(results),[False,True])
        self.assertEqual(User.objects.filter(email__iexact='same@example.test').count(),1)

    def test_signup_race_shows_field_error_instead_of_server_error(self):
        # Commit the competing account outside the savepoint rolled back below.
        original_valid=SignupForm.is_valid
        def racing_validation(form):
            valid=original_valid(form)
            if valid:User.objects.create_user('race-winner',email=form.cleaned_data['email'])
            return valid
        with patch.object(SignupForm,'is_valid',racing_validation):
            response=self.client.post('/accounts/signup/',self.data())
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Cette adresse e-mail est déjà utilisée.')
        self.assertEqual(User.objects.count(),1)

    def test_blank_emails_remain_allowed_and_email_changes_are_protected(self):
        first=User.objects.create_user('no-mail-one');User.objects.create_user('no-mail-two')
        User.objects.create_user('has-mail',email='same@example.test')
        first.email='SAME@example.test'
        with self.assertRaises(IntegrityError),transaction.atomic():first.save()
        with self.assertRaises(IntegrityError),transaction.atomic():
            User.objects.filter(pk=first.pk).update(email='same@example.test')
