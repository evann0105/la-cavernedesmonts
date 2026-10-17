from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from courriels.backend import LocalMailboxBackend
from courriels.messages import MODELS, build_message


class Command(BaseCommand):
    help = 'Capture les modèles d’e-mail dans la boîte locale, sans envoi réseau ni modification des comptes.'

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('Commande réservée au développement.')
        messages = []
        for kind in MODELS:
            details = 'Exemple : commande TEST-001\nPolaire · M · Bleu · 1 unité\nMontant fictif : 35,00 €' if kind in ('order','shipping','refund') else ''
            if kind == 'return_requested':
                details = 'Dossier TEST-RETOUR · 1 × Polaire · M · Bleu\nDéclaration de rétractation enregistrée. Renvoyez les articles dans les 14 jours suivant la déclaration.\nAdresse de retour :\n' + settings.RETURN_ADDRESS + '\nJoignez la référence du dossier au colis.\n' + settings.RETURN_COSTS_TEXT
            message = build_message(kind, 'client@example.test', 'https://example.test/exemple-sans-action', details, test=True, withdrawal_url='https://example.test/retractation' if kind=='order' else '')
            messages.append(message)
        count = LocalMailboxBackend().send_messages(messages)
        root = Path(settings.EMAIL_TEST_DIRECTORY)
        for kind, message in zip(MODELS, messages):
            path = root / (kind + '-apercu.html')
            path.write_text(message.alternatives[0].content, encoding='utf-8')
            path.chmod(0o600)
        self.stdout.write(self.style.SUCCESS(f'{count} modèles capturés localement. Aucun e-mail envoyé sur Internet.'))
        self.stdout.write('Boîte de test : /gestion/emails-test/')
