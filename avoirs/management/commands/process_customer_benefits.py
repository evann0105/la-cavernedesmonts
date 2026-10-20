from django.core.management.base import BaseCommand
from avoirs.mail import deliver_pending


class Command(BaseCommand):
    help = 'Réessaie les notifications transactionnelles des avoirs et bons clients en attente.'

    def handle(self, *args, **options):
        self.stdout.write(f'{deliver_pending()} notification(s) envoyée(s).')
