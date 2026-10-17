"""Summarise retention review candidates without outputting personal data."""
import json
from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder
from espace.privacy import retention_review


class Command(BaseCommand):
    help = 'Audit RGPD en lecture seule : aucun compte, commande, justificatif ou courriel supprimé.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', help='Rapport structuré sans identifiants personnels.')

    def handle(self, *args, **options):
        report = retention_review()
        if options['json']:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, cls=DjangoJSONEncoder))
            return
        self.stdout.write('Revue RGPD — lecture seule ; aucune modification.')
        for entry in report['categories']:
            self.stdout.write(f"{entry['nombre']} — {entry['libelle']}")
            self.stdout.write('  ' + entry['action'])
        self.stdout.write(report['attention'])
