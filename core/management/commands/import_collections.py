import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Product, Category


class Command(BaseCommand):
    help = "Importe les rubriques bébé du site d’origine, sans réseau ni doublons."

    @transaction.atomic
    def handle(self, *args, **options):
        data = json.loads((Path(settings.BASE_DIR) / 'core/collection_seed.json').read_text())
        categories = {}
        initialized = set()
        for entry in data['categories']:
            category, new_category = Category.objects.get_or_create(slug=entry['slug'], defaults={'name': entry['name']})
            categories[entry['slug']] = category
            if new_category:
                initialized.add(category.pk)
        for item in data['products']:
            category = categories[item['collection']]
            product, created = Product.objects.get_or_create(slug=item['slug'], defaults={
                'name': item['name'], 'price': item['price'], 'description': item['description'],
                'static_image': item['image'], 'category': category,
            })
            # Associate an existing sock with the baby collection, preserving its original category.
            # Only initialize membership once; merchant edits survive subsequent starts.
            if created or (item.get('existing') and category.pk in initialized):
                product.collections.add(category)
        self.stdout.write(self.style.SUCCESS('Rubriques bébé et offres spéciales prêtes.'))
