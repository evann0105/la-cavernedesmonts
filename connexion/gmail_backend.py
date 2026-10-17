"""Gmail API transport with send-only OAuth permission; SMTP remains the default."""
import base64
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import AuthorizedSession

SEND_SCOPE = 'https://www.googleapis.com/auth/gmail.send'


class EmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        sent = 0
        try:
            credentials = Credentials.from_authorized_user_file(settings.GMAIL_TOKEN_FILE, [SEND_SCOPE])
            with AuthorizedSession(credentials) as session:
                for email in email_messages:
                    if not email.recipients():
                        continue
                    message = email.message()
                    if email.bcc:
                        message['Bcc'] = ', '.join(email.bcc)
                    raw = base64.urlsafe_b64encode(message.as_bytes()).decode('ascii')
                    response = session.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send', json={'raw': raw}, timeout=15)
                    response.raise_for_status()
                    if not response.json().get('id'):
                        raise ValueError('Missing Gmail message identifier')
                    sent += 1
        except Exception:
            # Do not expose token paths, response bodies or credentials to logs/users.
            if not self.fail_silently:
                raise RuntimeError('Envoi Gmail indisponible. Vérifiez ou renouvelez la connexion Google.') from None
        return sent
