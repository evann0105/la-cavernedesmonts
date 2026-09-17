import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management import call_command
from core.models import Product, Category


class Command(BaseCommand):
    help = "Importe les 16 articles existants sans remplacer les modifications en base."

    def handle(self, *args, **options):
        source = Path(settings.BASE_DIR) / 'core/catalog_seed.json'
        for item in json.loads(source.read_text()):
            slug = item['slug']
            cat = 'Petits bonheurs' if 'chaussettes' in slug else ('Enfants' if 'urbaine' in slug else ('Hommes' if 'homme' in slug else 'Femmes'))
            cat_slug = {'Petits bonheurs': 'accessoires', 'Enfants': 'enfants', 'Hommes': 'hommes', 'Femmes': 'femmes'}[cat]
            category, _ = Category.objects.get_or_create(slug=cat_slug, defaults={'name': cat})
            description = 'Les petits plaisirs font les beaux souvenirs. Une touche de douceur pour prolonger les journées à la montagne jusque chez vous.' if 'chaussettes' in slug else 'L’air frais sur le visage, le plaisir de prendre son temps. Une pièce à emporter pour retrouver un peu de l’esprit montagne au fil des jours.'
            if 'softshell-homme' in slug:
                description += '\nBlouson Peak Mountain avec doublure intérieure en polaire. Coloris bleu marine, zips contrastants orange.'
            if 'urbaine' in slug:
                description += '\nVeste polaire garçon, 100 % polyester. Deux poches zippées, zip intégral avec protection du menton, finitions élastiques contrastantes.'
            Product.objects.get_or_create(slug=slug, defaults={'name': item['name'], 'price': item['price'], 'static_image': item['image'], 'description': description, 'category': category, 'is_featured': slug in ('veste-hybride-multi-matieres-femme', 'blouson-softshell-homme-impermeable', 'veste-polaire-de-montagne-beige', 'chaussettes-antiderapantes-marmottes-blanches'), 'sizes': 'S,M,L,XL,XXL,3XL' if 'softshell-homme' in slug else ''})
        call_command('import_collections', verbosity=options.get('verbosity', 1))
        call_command('import_femmes', verbosity=options.get('verbosity', 1))
        call_command('import_hommes', verbosity=options.get('verbosity', 1))
        self.stdout.write(self.style.SUCCESS('Catalogue importé ; tailles et disponibilités à valider dans l’administration.'))
