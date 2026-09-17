import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Category, Product


class Command(BaseCommand):
    help = 'Importe les 50 références Femme relevées sur le site d’origine le 17/09/2026.'

    @transaction.atomic
    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        data = json.loads((root / 'core/femmes_seed.json').read_text())
        originals = {p['slug']: p for p in json.loads((root / 'core/catalog_seed.json').read_text())}
        category, _ = Category.objects.get_or_create(slug='femmes', defaults={'name': 'Femmes'})
        created_count = updated_count = 0
        for item in data['products']:
            # Public source IDs, rather than names, distinguish different references/colorways.
            if Product.objects.filter(source_url=item['source_url']).exists():
                continue
            for image in item['images']:
                if not (root / 'core/static' / image).is_file():
                    raise CommandError(f"Photo manquante : {image}")
            product = Product.objects.filter(slug=item['slug']).first()
            if product is not None:
                if not item['existing'] or product.source_url:
                    raise CommandError(f"Conflit de référence : {item['slug']}")
                original = originals[item['slug']]
                # Enrich the original seed only; keep edits already made by the merchant.
                if product.name == original['name']:
                    product.name = item['name']
                if str(product.price) == original['price']:
                    product.price = item['price']
                initial_description = 'L’air frais sur le visage, le plaisir de prendre son temps. Une pièce à emporter pour retrouver un peu de l’esprit montagne au fil des jours.'
                if not product.description or product.description == initial_description:
                    product.description = item['description']
                product.source_url = item['source_url']
                product.save(update_fields=['name', 'price', 'description', 'source_url'])
                updated_count += 1
            else:
                Product.objects.create(
                    slug=item['slug'], source_url=item['source_url'], category=category,
                    name=item['name'], price=item['price'], description=item['description'],
                    static_image=item['image'], sizes='',
                )
                created_count += 1
        self.stdout.write(self.style.SUCCESS(
            f'Collection Femme : {created_count} articles ajoutés, {updated_count} fiches enrichies.'
        ))
