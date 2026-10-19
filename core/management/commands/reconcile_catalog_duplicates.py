"""Apply a reviewed duplicate manifest; dry-run is the default."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.catalog_deduplication import archive_verified_duplicate
from core.models import Product


class Command(BaseCommand):
    help = 'Vérifie ou archive les doublons documentés, sans supprimer les produits, les commandes ou les stocks.'

    def add_arguments(self, parser):
        parser.add_argument('--manifest', type=Path, default=Path(settings.BASE_DIR) / 'core/catalog_duplicates_seed.json')
        parser.add_argument('--apply', action='store_true', help='Appliquer les rapprochements validés ; sans cette option, aucune écriture.')

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            document = json.loads(options['manifest'].read_text())
        except (OSError, ValueError) as exc:
            raise CommandError('Le manifeste des doublons est absent ou invalide.') from exc
        entries = document.get('duplicates') if isinstance(document, dict) else None
        if not isinstance(entries, list):
            raise CommandError('Le manifeste doit contenir une liste « duplicates ».')
        changed = 0
        unchanged = 0
        for entry in entries:
            required = ('duplicate_slug', 'canonical_slug', 'duplicate_source_url', 'canonical_source_url')
            if not isinstance(entry, dict) or any(not isinstance(entry.get(key), str) or not entry[key] for key in required):
                raise CommandError('Une entrée ne précise pas les deux fiches et leurs adresses source.')
            try:
                duplicate = Product.objects.get(slug=entry['duplicate_slug'])
                canonical = Product.objects.get(slug=entry['canonical_slug'])
            except Product.DoesNotExist as exc:
                raise CommandError('Une fiche du manifeste est introuvable ; aucune modification du lot conservée.') from exc
            result = archive_verified_duplicate(
                duplicate, canonical,
                expected_source_url=entry['duplicate_source_url'],
                expected_target_url=entry['canonical_source_url'],
                evidence=entry.get('evidence') or {},
                apply=options['apply'],
            )
            if result.changed:
                changed += 1
            else:
                unchanged += 1
        action = 'archivé(s)' if options['apply'] else 'à archiver (simulation sans écriture)'
        self.stdout.write(f'{changed} doublon(s) {action} ; {unchanged} déjà traité(s).')
