from django.core.management.base import BaseCommand
from espace.legal import blockers

class Command(BaseCommand):
    help='Liste les informations et validations manquantes avant l’ouverture des ventes (sans certification juridique).'
    def handle(self,*args,**options):
        missing=blockers()
        for item in missing: self.stdout.write('À compléter : '+item)
        self.stdout.write(f'{len(missing)} point(s) bloquant(s). Une validation ne remplace pas les contrôles réels.')
