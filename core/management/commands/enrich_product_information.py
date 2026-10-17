"""Apply reviewed facts without overwriting a merchant's later corrections."""
import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import Product

FIELDS = ('brand','model_reference','composition','brand_contact')

class Command(BaseCommand):
    help='Complète les informations documentées des 26 fiches revues, sans toucher aux prix, stocks ou descriptions.'
    @transaction.atomic
    def handle(self,*args,**options):
        data=json.loads((Path(settings.BASE_DIR)/'core/product_information_seed.json').read_text())
        changed=0;skipped=0
        for entry in data['products']:
            product=Product.objects.select_for_update().filter(slug=entry['slug']).first()
            if not product:continue
            if entry['source_url'] and product.source_url != entry['source_url']:
                skipped+=1;continue
            evidence=dict(product.information_sources)
            updates=[]
            for field,value in entry['facts'].items():
                if field not in FIELDS:raise CommandError('Champ d’information inconnu.')
                current=getattr(product,field)
                previous=evidence.get(field,{}).get('value')
                if field in evidence and current != previous:continue
                if field not in evidence and current and current != value:continue
                proof=entry['evidence'][field]
                if not proof['sources'] or any(not s['url'].startswith('https://') for s in proof['sources']):raise CommandError('Source HTTPS manquante.')
                if current != value:setattr(product,field,value);updates.append(field)
                evidence[field]=proof
            previous_review=evidence.get('_review')
            if (previous_review is None and not product.information_review_notes) or (previous_review is not None and product.information_review_notes == previous_review.get('notes')):
                if product.information_review_notes != entry['review_notes']:
                    product.information_review_notes=entry['review_notes'];updates.append('information_review_notes')
            evidence['_review']={'checked_on':data['checked_on'],'notes':entry['review_notes'],'composition_status':entry['composition_status']}
            if evidence != product.information_sources:
                product.information_sources=evidence;updates.append('information_sources')
            # Notes and deleted facts can be edited without being restored at next startup.
            if updates:
                product.save(update_fields=updates);changed+=1
        self.stdout.write(f'{changed} fiche(s) complétée(s) ; {skipped} référence(s) différente(s) ignorée(s).')
