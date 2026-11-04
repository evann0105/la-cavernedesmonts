"""Create a truthful starting point, never invent pre-existing history."""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Conserver l’état actuel des produits et de l’accueil avant les premières modifications.'

    def handle(self, *args, **options):
        from core.models import Product
        from historique.models import HistoryVersion
        from historique.products import record_product
        from historique.homepage import record_homepage
        count = 0
        for pk in Product.objects.order_by('pk').values_list('pk', flat=True).iterator():
            with transaction.atomic():
                product = Product.objects.select_for_update().get(pk=pk)
                if not HistoryVersion.objects.filter(scope='product', object_id=pk).exists():
                    record_product(product, label='État initial conservé', source='baseline')
                    count += 1
        with transaction.atomic():
            from accueil.services import _locked
            _locked(None)
            if not HistoryVersion.objects.filter(scope='homepage', object_id=1).exists():
                record_homepage(label='État initial conservé', source='baseline')
                count += 1
        self.stdout.write(self.style.SUCCESS(f'{count} état(s) initial(aux) conservé(s).'))
