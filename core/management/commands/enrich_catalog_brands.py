"""Import reviewed brand evidence without replacing the merchant's decisions."""

import json
import re
from copy import deepcopy
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Product


def _https_url(value):
    if not isinstance(value, str) or any(ord(char) < 33 for char in value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme == 'https' and parsed.hostname
            and not parsed.username and not parsed.password
        )
    except ValueError:
        return False


def _review_date(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _validated_manifest(path):
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise CommandError(f'Impossible de lire le manifeste des marques : {exc}') from exc
    if not isinstance(manifest, dict) or not _review_date(manifest.get('checked_on')):
        raise CommandError('Le manifeste doit contenir une date checked_on au format AAAA-MM-JJ.')
    entries = manifest.get('products')
    if not isinstance(entries, list):
        raise CommandError('Le manifeste doit contenir une liste products.')
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise CommandError('Chaque fiche du manifeste doit être un objet.')
        slug, name = entry.get('slug'), entry.get('expected_name')
        if not isinstance(slug, str) or not slug or not isinstance(name, str) or not name:
            raise CommandError('Chaque fiche doit préciser son slug et son nom exact attendu.')
        if slug in seen:
            raise CommandError(f'Référence présente plusieurs fois dans le manifeste : {slug}.')
        seen.add(slug)
        if 'source_url' not in entry or (
            entry['source_url'] is not None and not isinstance(entry['source_url'], str)
        ):
            raise CommandError(f'Adresse d’origine invalide pour {slug}.')
        if not isinstance(entry.get('review_notes', ''), str):
            raise CommandError(f'Notes de vérification invalides pour {slug}.')
        if 'brand' not in entry:
            raise CommandError(f'Marque ou valeur null manquante pour {slug}.')
        brand = entry['brand']
        evidence = entry.get('evidence', {})
        if not isinstance(evidence, dict):
            raise CommandError(f'Preuve invalide pour {slug}.')
        if brand is None:
            # An unresolved identity is recorded privately, never as a public brand.
            continue
        if (
            not isinstance(brand, str) or not brand or brand != brand.strip()
            or len(brand) > 100 or any(ord(char) < 32 for char in brand)
        ):
            raise CommandError(f'Marque invalide pour {slug}.')
        if evidence.get('value') != brand:
            raise CommandError(f'La preuve ne correspond pas à la marque de {slug}.')
        if not _review_date(evidence.get('checked_on')):
            raise CommandError(f'Date de la preuve invalide pour {slug}.')
        if not isinstance(evidence.get('basis'), str) or not evidence['basis'].strip():
            raise CommandError(f'Justification de la marque manquante pour {slug}.')
        sources = evidence.get('sources')
        if not isinstance(sources, list) or not sources:
            raise CommandError(f'Source HTTPS manquante pour {slug}.')
        for source in sources:
            if (
                not isinstance(source, dict) or not _https_url(source.get('url'))
                or not isinstance(source.get('label'), str) or not source['label'].strip()
            ):
                raise CommandError(f'Source HTTPS ou libellé invalide pour {slug}.')
        digest = evidence.get('source_snapshot_sha256')
        if digest is not None and (
            not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', digest)
        ):
            raise CommandError(f'Empreinte de source invalide pour {slug}.')
    return manifest


class Command(BaseCommand):
    help = 'Prépare les marques documentées ; utiliser --apply pour enregistrer les modifications.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Enregistrer les marques et preuves vérifiées.')
        parser.add_argument('--manifest', type=Path, help='Utiliser un autre manifeste JSON de vérification.')

    def handle(self, *args, **options):
        path = options.get('manifest') or Path(settings.BASE_DIR) / 'core/catalog_brands_seed.json'
        manifest = _validated_manifest(Path(path))
        counts = {'brands': 0, 'unconfirmed': 0, 'unchanged': 0, 'identity': 0, 'merchant': 0}
        with transaction.atomic():
            for entry in manifest['products']:
                product = Product.objects.select_for_update().filter(slug=entry['slug']).first()
                if (
                    product is None or product.name != entry['expected_name']
                    or product.source_url != entry['source_url']
                ):
                    counts['identity'] += 1
                    continue
                existing = product.information_sources
                # A historical proof remains a deliberate guard even if the merchant
                # has emptied the corresponding field since its first import.
                if product.brand or not isinstance(existing, dict) or 'brand' in existing:
                    counts['merchant'] += 1
                    continue
                information = deepcopy(existing)
                brand = entry['brand']
                information['_brand_review'] = {
                    'status': 'verified' if brand is not None else 'unconfirmed',
                    'checked_on': manifest['checked_on'],
                    'notes': entry.get('review_notes', ''),
                    'evidence': deepcopy(entry.get('evidence', {})),
                }
                fields = ['information_sources']
                if brand is not None:
                    information['brand'] = deepcopy(entry['evidence'])
                    product.brand = brand
                    fields.append('brand')
                if information == existing:
                    counts['unchanged'] += 1
                    continue
                product.information_sources = information
                if options['apply']:
                    product.save(update_fields=fields)
                counts['brands' if brand is not None else 'unconfirmed'] += 1
        mode = 'Enregistré' if options['apply'] else 'Simulation — aucune modification enregistrée'
        self.stdout.write(
            f"{mode} : {counts['brands']} marque(s) documentée(s), "
            f"{counts['unconfirmed']} identité(s) à confirmer, "
            f"{counts['unchanged']} fiche(s) inchangée(s), "
            f"{counts['identity']} référence(s) différente(s) ou absente(s) ignorée(s), "
            f"{counts['merchant']} choix ou preuve(s) antérieure(s) conservé(s)."
        )
