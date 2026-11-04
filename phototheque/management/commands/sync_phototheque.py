from django.core.management.base import BaseCommand

from phototheque.inventory import sync_inventory


class Command(BaseCommand):
    help = 'Conserver dans la bibliothèque les photos référencées par les produits, l’accueil et les pages.'

    def handle(self, *args, **options):
        result = sync_inventory()
        self.stdout.write(f"Bibliothèque : {result['created']} photo(s) ajoutée(s), {result['existing']} déjà conservée(s), {result['failed']} indisponible(s).")
        if options['verbosity'] > 1:
            for error in result['errors']:
                self.stderr.write(f"{error['source']} : {error['message']}")
