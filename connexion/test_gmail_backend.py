import base64
from unittest.mock import patch
from django.core.mail import EmailMessage
from django.test import SimpleTestCase, override_settings
from connexion.gmail_backend import EmailBackend


@override_settings(GMAIL_TOKEN_FILE='/private/not-read-in-tests.json')
class GmailBackendTests(SimpleTestCase):
    @patch('connexion.gmail_backend.Credentials.from_authorized_user_file')
    @patch('connexion.gmail_backend.AuthorizedSession')
    def test_sends_mime_message_and_counts_acceptance(self, session, credentials):
        post=session.return_value.__enter__.return_value.post
        post.return_value.json.return_value={'id':'accepted'}
        message=EmailMessage('Confirmation','Lien de confirmation','shop@example.test',['client@example.test'])
        self.assertEqual(EmailBackend().send_messages([message]),1)
        args,kwargs=post.call_args
        self.assertEqual(args[0],'https://gmail.googleapis.com/gmail/v1/users/me/messages/send')
        self.assertIn(b'client@example.test',base64.urlsafe_b64decode(kwargs['json']['raw']))
        self.assertEqual(credentials.call_args.args[1],['https://www.googleapis.com/auth/gmail.send'])

    @patch('connexion.gmail_backend.Credentials.from_authorized_user_file',side_effect=ValueError('secret credential'))
    def test_errors_do_not_leak_credentials_or_claim_success(self, credentials):
        message=EmailMessage('Test','Test','shop@example.test',['client@example.test'])
        with self.assertRaises(RuntimeError) as caught:
            EmailBackend().send_messages([message])
        self.assertNotIn('secret credential',str(caught.exception))
        self.assertEqual(EmailBackend(fail_silently=True).send_messages([message]),0)
        self.assertEqual(EmailBackend().send_messages([]),0)
