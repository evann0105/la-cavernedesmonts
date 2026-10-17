import csv
import io
from django.contrib.admin.models import LogEntry
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from core.models import Product
from .models import Variant
from .importing import COLUMNS, ImportInvalid, parse_inventory, process_inventory, export_inventory


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
class InventoryImportTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user('import-manager', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(codename='manage_catalog'))
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.product = Product.objects.create(name='Polaire test', price='35.00')

    def csv(self, rows):
        output=io.StringIO(); writer=csv.writer(output, delimiter=';');writer.writerow(COLUMNS);writer.writerows(rows)
        return output.getvalue().encode('utf-8')

    def row(self, **changes):
        data=dict(zip(COLUMNS,[self.product.pk,self.product.name,'',0,'M','Bleu','REF',5]));data.update(changes)
        return [data[k] for k in COLUMNS]

    def test_preview_then_apply_and_atomic_failure(self):
        rows=parse_inventory(self.csv([self.row()]))
        process_inventory(rows,self.manager)
        self.assertFalse(Variant.objects.exists())
        process_inventory(rows,self.manager,apply=True)
        self.assertEqual(Variant.objects.get().quantity,5)
        self.assertEqual(LogEntry.objects.filter(user=self.manager).count(),1)
        with self.assertRaises(ImportInvalid):
            process_inventory(parse_inventory(self.csv([self.row(taille='L'),self.row(produit_id=999999)])),self.manager,apply=True)
        self.assertEqual(Variant.objects.count(),1)

    def test_duplicate_negative_unknown_and_cross_product(self):
        for rows in ([self.row(), self.row(taille=' m ',couleur='BLEU')], [self.row(quantite=-1)], [self.row(taille='')], [self.row(variante_id=999999)]):
            with self.assertRaises(ImportInvalid):
                process_inventory(parse_inventory(self.csv(rows)),self.manager,apply=True)
        self.assertFalse(Variant.objects.exists())
        process_inventory(parse_inventory(self.csv([self.row(quantite='')])),self.manager,apply=True)
        self.assertIsNone(Variant.objects.get().quantity)

    def test_reservations_and_stale_revision(self):
        variant=Variant.objects.create(product=self.product,size='M',color='Bleu',quantity=5,reserved=3,revision=2)
        for kwargs in ({'quantite':2}, {'quantite':''}, {'revision':1}):
            row=self.row(variante_id=variant.pk,revision=2)
            values=dict(zip(COLUMNS,row));values.update(kwargs)
            with self.assertRaises(ImportInvalid):
                process_inventory(parse_inventory(self.csv([[values[k] for k in COLUMNS]])),self.manager,apply=True)
        process_inventory(parse_inventory(self.csv([self.row(variante_id=variant.pk,revision=2,quantite=6)])),self.manager,apply=True)
        variant.refresh_from_db();self.assertEqual((variant.quantity,variant.reserved,variant.revision),(6,3,3))

    def test_confirmation_integrity_and_permissions(self):
        url=reverse('stocks:import')
        response=self.client.post(url,{'file':SimpleUploadedFile('stock.csv',self.csv([self.row()]))})
        token=response.context['confirmation']
        self.assertFalse(Variant.objects.exists())
        self.assertContains(self.client.post(url,{'confirmation':token+'x'}),'invalide')
        self.assertFalse(Variant.objects.exists())
        self.assertRedirects(self.client.post(url,{'confirmation':token}),reverse('stocks:overview'))
        self.assertEqual(Variant.objects.count(),1)
        self.assertContains(self.client.post(url,{'confirmation':token}),'existent déjà')
        self.client.logout()
        self.assertEqual(self.client.get(reverse('stocks:export')).status_code,302)
        self.assertEqual(self.client.post(url,{'confirmation':token}).status_code,302)

    def test_export_roundtrip_no_write_and_formula_protection(self):
        Variant.objects.create(product=self.product,size='M',color='Bleu',reference='REF',quantity=5,revision=2)
        process_inventory(parse_inventory(export_inventory().encode('utf-8')),self.manager,apply=True)
        self.assertEqual(Variant.objects.get().revision,2)
        self.product.name='=HYPERLINK("bad")';self.product.save()
        self.assertIn("'=HYPERLINK",export_inventory())

    def test_changed_stock_after_preview_rejects_confirmation(self):
        variant=Variant.objects.create(product=self.product,size='M',color='Bleu',quantity=5,revision=2)
        url=reverse('stocks:import')
        response=self.client.post(url,{'file':SimpleUploadedFile('stock.csv',self.csv([self.row(variante_id=variant.pk,revision=2,quantite=9)]))})
        variant.quantity=4;variant.revision=3;variant.save()
        self.assertContains(self.client.post(url,{'confirmation':response.context['confirmation']}),'modifié depuis')
        variant.refresh_from_db();self.assertEqual(variant.quantity,4)

    def test_regular_customer_denied(self):
        from django.contrib.auth import get_user_model
        customer=get_user_model().objects.create_user('import-customer')
        self.client.force_login(customer,backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(reverse('stocks:export')).status_code,403)
        self.assertEqual(self.client.post(reverse('stocks:import'),{'file':SimpleUploadedFile('stock.csv',self.csv([self.row()]))}).status_code,403)
