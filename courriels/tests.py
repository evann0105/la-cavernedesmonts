import json
import uuid
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from connexion.email_verification import send_verification, confirm_token
from .backend import LocalMailboxBackend
from .messages import MODELS, build_message


@override_settings(DEBUG=True, EMAIL_TEST_MODE=True, LOCAL_SKIP_EMAIL_VERIFICATION=True, DEFAULT_FROM_EMAIL='boutique@example.test')
class MailboxTests(TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        override=override_settings(EMAIL_TEST_DIRECTORY=directory.name);override.enable();self.addCleanup(override.disable)
        self.root=Path(directory.name)
        self.manager=get_user_model().objects.create_user('mail-manager',is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(codename='manage_catalog'))
        self.client.force_login(self.manager,backend='connexion.backends.EmailOrUsernameBackend')

    def test_six_messages_are_local_multipart_and_escaped(self):
        with patch('smtplib.SMTP', side_effect=AssertionError('No SMTP allowed')):
            call_command('test_emails')
        self.assertEqual(len(list(self.root.glob('*.json'))),len(MODELS))
        for path in self.root.glob('*.json'):
            item=json.loads(path.read_text())
            self.assertIn('[TEST]',item['subject']);self.assertIn('https://example.test/',item['body'])
            self.assertIn('<html',item['html']);self.assertEqual(path.stat().st_mode & 0o777,0o600)
        message=build_message('order','client@example.test','https://example.test/',details='<script>alert(1)</script>')
        self.assertNotIn('<script>',message.alternatives[0].content)
        self.assertIn('&lt;script&gt;',message.alternatives[0].content)
        self.assertContains(self.client.get(reverse('test_email_inbox')),'Votre commande est confirmée')
        path=next(self.root.glob('*.json'))
        response=self.client.get(reverse('test_email_preview',args=[uuid.UUID(path.stem)]))
        self.assertEqual(response.status_code,200);self.assertIn('sandbox',response['Content-Security-Policy'])

    def test_inbox_requires_admin_and_is_disabled_in_production(self):
        with override_settings(DEBUG=False):
            self.assertEqual(self.client.get(reverse('test_email_inbox')).status_code,404)
            with self.assertRaises(ImproperlyConfigured):
                LocalMailboxBackend().send_messages([build_message('contact','a@example.test','https://example.test/')])
        with override_settings(EMAIL_TEST_MODE=False):
            self.assertEqual(self.client.get(reverse('test_email_inbox')).status_code,404)
        customer=get_user_model().objects.create_user('customer')
        self.client.force_login(customer,backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(reverse('test_email_inbox')).status_code,403)
        self.client.logout();self.assertEqual(self.client.get(reverse('test_email_inbox')).status_code,302)

    @override_settings(EMAIL_BACKEND='courriels.backend.LocalMailboxBackend',EMAIL_DELIVERY_ENABLED=True,SITE_URL='http://localhost:8001')
    def test_verification_really_reaches_local_box_and_token_works_once(self):
        import re
        customer=get_user_model().objects.create_user('verify-test',email='customer@example.test')
        self.assertEqual(send_verification(customer),'sent')
        item=json.loads(next(self.root.glob('*.json')).read_text())
        self.assertEqual(item['to'],['customer@example.test'])
        token=re.search(r'/confirmer-email/([^/]+)/',item['body']).group(1)
        self.assertIn(token,item['html'])
        self.assertTrue(confirm_token(customer,token));self.assertFalse(confirm_token(customer,token))
