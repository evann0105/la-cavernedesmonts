"""Customer-facing justification, frozen history and validity text."""
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import TestCase
from django.utils import timezone

from espace.privacy import subject_export
from .mail import conditions
from .models import CustomerBenefit
from .services import BenefitError, quote, reserve_locked, release_locked, save_benefit
from .test_domain import BenefitFixtures


class BenefitPresentationTests(BenefitFixtures, TestCase):
    def test_personal_justification_is_encrypted_and_frozen_outside_payment_json(self):
        message = 'Votre retour a pris du retard.\nMerci pour votre patience.'
        benefit = self.benefit(justification=message)
        order = self.order()
        quoted = quote(benefit, self.client_user, order.subtotal)
        self.assertNotIn(message, str(quoted['snapshot']))
        with transaction.atomic():
            redemption = reserve_locked(order, benefit, self.client_user)
        self.assertEqual(redemption.justification_snapshot, message)
        with connection.cursor() as cursor:
            cursor.execute('SELECT justification FROM avoirs_customerbenefit WHERE id=%s', [benefit.pk])
            stored = cursor.fetchone()[0]
            cursor.execute('SELECT justification_snapshot FROM avoirs_benefitredemption WHERE id=%s', [redemption.pk])
            frozen = cursor.fetchone()[0]
        for ciphertext in (stored, frozen):
            self.assertTrue(ciphertext.startswith('enc:v1:'))
            self.assertNotIn(message, ciphertext)
        data = subject_export(self.client_user)
        self.assertEqual(data['avoirs_et_bons_clients'][0]['justification'], message)
        self.assertEqual(data['commandes'][0]['avantage_client']['justification'], message)
        benefit.justification = 'Autre explication'
        with self.assertRaises(BenefitError):
            save_benefit(benefit, self.staff, notify=False)
        with transaction.atomic():
            release_locked(order)
        save_benefit(benefit, self.staff, notify=False)
        redemption.refresh_from_db()
        self.assertEqual(redemption.justification_snapshot, message)
        self.assertEqual(subject_export(self.client_user)['commandes'][0]['avantage_client']['justification'], message)

    def test_justification_is_in_customer_email_with_creation_date(self):
        benefit = self.benefit(justification='Pour vous remercier de votre fidélité.\nÀ bientôt à la boutique !')
        details = conditions(benefit)
        self.assertIn('Message de la boutique :\n' + benefit.justification, details)
        self.assertIn('Créé le ' + timezone.localtime(benefit.created_at).strftime('%d/%m/%Y'), details)
        self.assertIn(benefit.code, details)

    def test_existing_codes_can_have_no_justification_and_long_notes_are_rejected(self):
        self.assertEqual(self.benefit().justification, '')
        with self.assertRaises(ValidationError):
            self.benefit(justification='x' * 2001)

    def test_editing_justification_preserves_code_and_changes_revision(self):
        benefit = self.benefit(justification='Motif initial')
        code, revision = benefit.code, benefit.updated_at
        benefit.justification = 'Motif corrigé par la vendeuse'
        save_benefit(benefit, self.staff, notify=False)
        benefit.refresh_from_db()
        self.assertEqual(benefit.code, code)
        self.assertNotEqual(benefit.updated_at, revision)
        self.assertEqual(benefit.justification, 'Motif corrigé par la vendeuse')

    def test_validity_text_covers_unlimited_scheduled_remaining_and_expired(self):
        now = timezone.now()
        with patch('avoirs.models.timezone.now', return_value=now):
            unlimited = self.benefit()
            self.assertEqual(unlimited.validity_label, 'Sans date limite d’utilisation.')
            for duration, words in ((timedelta(seconds=30), 'moins d’une minute'),
                                    (timedelta(minutes=5), '5 minutes'),
                                    (timedelta(hours=2), '2 heures'),
                                    (timedelta(days=7), '7 jours')):
                with self.subTest(duration=duration):
                    benefit = self.benefit(ends_at=now + duration)
                    self.assertEqual(benefit.validity_label, f'Temps restant : {words}.')
            future = self.benefit(starts_at=now + timedelta(days=3), ends_at=now + timedelta(days=10))
            self.assertEqual(future.validity_label, 'Durée d’utilisation : 7 jours à partir du début de validité.')
            expired = self.benefit(ends_at=now - timedelta(seconds=1))
            self.assertEqual(expired.validity_label, 'La période de validité est terminée.')
            inactive = self.benefit(active=False)
            self.assertEqual(inactive.validity_label, 'Ce code est désactivé.')
