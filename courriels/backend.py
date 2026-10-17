"""Private local mailbox. Never opens a network connection."""
import json
import os
import uuid
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend
from django.utils import timezone


class LocalMailboxBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        if not settings.DEBUG:
            raise ImproperlyConfigured('La boîte de test est réservée au développement.')
        root = Path(settings.EMAIL_TEST_DIRECTORY)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        count = 0
        for message in email_messages or []:
            html = next((content for content, mimetype in getattr(message, 'alternatives', []) if mimetype == 'text/html'), '')
            payload = {'subject':message.subject, 'to':message.to, 'from':message.from_email, 'body':message.body, 'html':html, 'date':timezone.now().isoformat()}
            name = uuid.uuid4().hex + '.json'
            fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(payload, stream, ensure_ascii=False)
            count += 1
        return count
