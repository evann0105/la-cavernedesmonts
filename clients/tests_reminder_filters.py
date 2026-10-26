from datetime import date, timedelta
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from .models import SupportCase


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ReminderFilterTests(TestCase):
    def setUp(self):
        self.seller = get_user_model().objects.create_user('reminder-seller', email='reminder@example.test', is_staff=True)
        self.seller.user_permissions.add(Permission.objects.get(codename='manage_returns'))
        verify_test_user(self.seller)
        self.client.force_login(self.seller, backend='connexion.backends.EmailOrUsernameBackend')
        self.today = date(2026, 10, 26)
        self.patcher = patch('clients.views.timezone.localdate', return_value=self.today)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.overdue = self.case('Dossier en retard', self.today - timedelta(days=1))
        self.current = self.case('Rappel du jour', self.today)
        self.future = self.case('Rappel futur', self.today + timedelta(days=1))
        self.no_date = self.case('Sans date', None)
        self.resolved = self.case('Déjà résolu', self.today, state='resolved', resolved_at=timezone.now())

    def case(self, subject, due_date, **extra):
        return SupportCase.objects.create(creation_key=uuid.uuid4(), creation_fingerprint='f' * 64,
            customer_name='Client exemple', phone='0600000000', subject=subject,
            description='Demande de rappel.', due_date=due_date, **extra)

    def rows(self, **filters):
        response = self.client.get(reverse('clients:case_list'), filters)
        self.assertEqual(response.status_code, 200)
        return {item.pk for item in response.context['page_obj']}, response

    def test_dashboard_date_links_exclude_resolved_and_other_days(self):
        for deadline, expected in [('today', {self.current.pk}), ('overdue', {self.overdue.pk}),
                                   ('due', {self.current.pk, self.overdue.pk})]:
            with self.subTest(deadline=deadline):
                rows, response = self.rows(echeance=deadline, etat='all')
                self.assertEqual(rows, expected)
                self.assertEqual(response.context['deadline'], deadline)
                self.assertContains(response, 'echeance=' + deadline)

    def test_old_reminder_link_and_clearing_filter(self):
        rows, response = self.rows(relance='1')
        self.assertEqual(rows, {self.current.pk, self.overdue.pk})
        self.assertIn('relance=1', response.context['filter_query'])
        for deadline in ('', 'invalid'):
            rows, _ = self.rows(echeance=deadline)
            self.assertEqual(rows, {self.current.pk, self.overdue.pk, self.future.pk, self.no_date.pk})

    def test_priority_combines_with_date_and_pagination_keeps_filter(self):
        SupportCase.objects.filter(pk=self.current.pk).update(priority='urgent')
        self.assertEqual(self.rows(echeance='today', priorite='urgent')[0], {self.current.pk})
        for i in range(25):
            self.case(f'Autre rappel {i}', self.today)
        rows, response = self.rows(echeance='today', page=2)
        self.assertEqual(len(rows), 1)
        self.assertContains(response, 'echeance=today&amp;page=1')

    def test_correction_returns_to_case_and_detail_contains_private_documents(self):
        response = self.client.get(reverse('clients:case_edit', args=[self.current.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['back_url'], reverse('clients:case_detail', args=[self.current.pk]))
        detail = self.client.get(reverse('clients:case_detail', args=[self.current.pk]))
        self.assertContains(detail, 'Corriger le dossier')
        self.assertContains(detail, 'Pièces jointes')
        self.assertContains(detail, reverse('clients:attachment_add', args=[self.current.pk]))
