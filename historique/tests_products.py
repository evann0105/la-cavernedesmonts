import tempfile
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from connexion.test_helpers import verify_test_user
from core.models import Category, Product, ProductColor, ProductImage, PriceObservation
from stocks.models import Variant, VariantLabelCorrection
from stocks.importing import process_inventory
from historique.exceptions import HistoryError, HistoryConflict
from historique.models import HistoryVersion
from historique.products import (snapshot_product, record_product, record_change, product_change,
    restore_product, validate_restore, restore_warnings, editor_token, validate_editor_token)
from historique.services import fingerprint, prepare_restore, apply_restore


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ProductHistoryTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory(prefix='product-history-')
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable(); self.addCleanup(self.settings_override.disable)
        self.manager = get_user_model().objects.create_user('history-manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(self.manager)
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.category = Category.objects.create(name='Historique femme', slug='historique-femme')
        self.product = Product.objects.create(name='Polaire conservée', category=self.category,
            price='80.00', description='Description d’origine', is_published=False,
            main_image=SimpleUploadedFile('original.webp', b'original-photo', content_type='image/webp'),
            main_color='Vert', include_imported_gallery=False,
            information_review_notes='NOTE PRIVÉE HISTORIQUE',
            information_sources={'brand': {'value': 'Marque', 'sources': []}})
        self.variant = Variant.objects.create(product=self.product, size='M', color='Vert',
            quantity=12, reserved=2, price='70.00', reference='VERT-M')
        self.gallery = ProductImage.objects.create(product=self.product,
            image=SimpleUploadedFile('gallery.webp', b'gallery-photo'), alt='Ancienne photo', order=3)
        self.color = ProductColor.objects.create(product=self.product, name='Vert',
            image=SimpleUploadedFile('vert.webp', b'color-photo'))
        self.product.refresh_from_db(); self.variant.refresh_from_db()

    def version(self):
        return record_product(self.product, actor=self.manager, source='baseline')

    def restore(self, version, **kwargs):
        return restore_product(version, actor=kwargs.pop('actor', self.manager),
            expected_fingerprint=kwargs.pop('expected_fingerprint', fingerprint(snapshot_product(self.product))), **kwargs)

    def test_snapshot_excludes_inventory_and_identity_and_is_encrypted(self):
        version = self.version()
        state = version.snapshot
        self.assertEqual(state['variants'][0], {'id': self.variant.pk, 'size': 'M', 'color': 'Vert',
            'reference': 'VERT-M', 'price': '70.00'})
        self.assertNotIn('slug', state['fields']); self.assertNotIn('source_url', state['fields'])
        with connection.cursor() as cursor:
            cursor.execute('SELECT snapshot FROM historique_historyversion WHERE id = %s', [version.pk])
            raw = cursor.fetchone()[0]
        self.assertNotIn('NOTE PRIVÉE HISTORIQUE', str(raw))
        version.refresh_from_db()
        self.assertEqual(version.snapshot['fields']['information_review_notes'], 'NOTE PRIVÉE HISTORIQUE')

    def test_quantity_reservations_and_sales_do_not_create_editorial_versions(self):
        before = snapshot_product(self.product)
        Variant.objects.filter(pk=self.variant.pk).update(quantity=9, reserved=1, revision=22)
        self.assertEqual(fingerprint(before), fingerprint(snapshot_product(self.product)))
        self.assertIsNone(record_change(self.product, before, actor=self.manager))
        self.assertFalse(HistoryVersion.objects.exists())

    def test_restoration_preserves_live_inventory_newer_variants_and_sales_prices(self):
        version = self.version()
        self.product.name='Nouvelle polaire'; self.product.price='99.00'; self.product.save()
        self.variant.quantity=7; self.variant.reserved=1; self.variant.revision=9; self.variant.price='90.00'; self.variant.save()
        new = Variant.objects.create(product=self.product, size='XL', color='Rouge', quantity=4, reserved=0, price='125.00')
        result = self.restore(version)
        self.product.refresh_from_db(); self.variant.refresh_from_db(); new.refresh_from_db()
        self.assertEqual((self.product.name, self.product.price), ('Polaire conservée', Decimal('80.00')))
        self.assertEqual((self.variant.quantity, self.variant.reserved, self.variant.revision, self.variant.price), (7,1,10,Decimal('70.00')))
        self.assertEqual((new.quantity, new.price), (4,Decimal('125.00')))
        self.assertEqual(result.restored_from_id, version.pk)
        self.assertEqual(result.source, 'restore')
        self.assertEqual(PriceObservation.objects.filter(variant=self.variant).latest('pk').price, Decimal('70.00'))
        self.assertTrue(any('ajoutée' in warning for warning in restore_warnings(version)))

    def test_restoration_recovers_deleted_photos_from_archive_with_stable_ids(self):
        version=self.version()
        old_name=self.product.main_image.name
        Path(self.product.main_image.path).unlink()
        self.product.main_image=''; self.product.save(update_fields=['main_image'])
        self.product.images.all().delete(); self.product.color_photos.all().delete()
        result=self.restore(version)
        self.product.refresh_from_db()
        self.assertNotEqual(self.product.main_image.name, old_name)
        self.assertTrue(self.product.main_image.name.startswith('historique-assets/'))
        self.assertTrue(ProductImage.objects.filter(pk=self.gallery.pk, alt='Ancienne photo').exists())
        self.assertTrue(ProductColor.objects.filter(pk=self.color.pk, name='Vert').exists())
        self.assertEqual(self.product.main_image.read(), b'original-photo')

    def test_missing_original_and_archive_blocks_without_mutation(self):
        version=self.version()
        for asset in version.assets:
            if asset['name'] == self.product.main_image.name:
                Path(self.media.name,asset['name']).unlink(); Path(self.media.name,asset['archive']).unlink()
        self.product.name='Actuel'; self.product.save(update_fields=['name'])
        before=snapshot_product(self.product)
        with self.assertRaises(HistoryError): self.restore(version)
        self.assertEqual(snapshot_product(self.product),before)

    def test_current_identity_and_import_source_are_never_restored(self):
        version=self.version()
        self.product.slug='identite-conservee'; self.product.source_url='https://example.test/source'; self.product.save()
        self.restore(version); self.product.refresh_from_db()
        self.assertEqual(self.product.slug,'identite-conservee')
        self.assertEqual(self.product.source_url,'https://example.test/source')

    def test_missing_variant_blocks_whole_restore(self):
        version=self.version(); self.variant.delete()
        self.product.name='Actuel'; self.product.save(update_fields=['name'])
        with self.assertRaisesMessage(HistoryError,'supprimée'): self.restore(version)
        self.product.refresh_from_db(); self.assertEqual(self.product.name,'Actuel')

    def test_variant_identity_held_payment_blocks_but_price_only_is_allowed(self):
        version=self.version()
        Variant.objects.filter(pk=self.variant.pk).update(size='L')
        with self.assertRaisesMessage(HistoryError,'paiement'): self.restore(version)
        Variant.objects.filter(pk=self.variant.pk).update(reserved=0)
        self.restore(version)
        self.variant.refresh_from_db(); self.assertEqual(self.variant.size,'M')
        self.assertTrue(VariantLabelCorrection.objects.filter(variant=self.variant,previous_size='L',corrected_size='M').exists())

    def test_collision_with_new_variant_blocks(self):
        version=self.version()
        Variant.objects.filter(pk=self.variant.pk).update(size='L',reserved=0)
        Variant.objects.create(product=self.product,size='M',color='Vert',quantity=3)
        with self.assertRaisesMessage(HistoryError,'autre variante'): self.restore(version)

    def test_archived_category_and_invalid_publication_block(self):
        version=self.version()
        from django.utils import timezone
        Category.objects.filter(pk=self.category.pk).update(archived_at=timezone.now())
        with self.assertRaisesMessage(HistoryError,'rubrique'): validate_restore(version)
        Category.objects.filter(pk=self.category.pk).update(archived_at=None)
        self.product.is_published=True; self.product.description=''; self.product.save()
        bad=self.version()
        with self.assertRaisesMessage(HistoryError,'incomplète'): validate_restore(bad)

    def test_duplicate_cannot_be_republished_and_promotion_is_untouched(self):
        self.product.is_published=True; self.product.save(); version=self.version()
        canonical=Product.objects.create(name='Canonique',price=10,is_published=False)
        self.product.duplicate_of=canonical; self.product.is_published=False; self.product.save()
        with self.assertRaisesMessage(HistoryError,'doublon'): self.restore(version)

    def test_history_failure_rolls_back_content_price_observations_and_metadata(self):
        version=self.version()
        self.product.name='Actuel'; self.product.price='120.00'; self.product.save()
        before=snapshot_product(self.product); observations=PriceObservation.objects.count()
        with patch('historique.products.record_product',side_effect=RuntimeError('journal unavailable')):
            with self.assertRaises(RuntimeError): self.restore(version)
        self.assertEqual(snapshot_product(self.product),before)
        self.assertEqual(PriceObservation.objects.count(),observations)

    def test_history_failure_after_mutation_rolls_back_everything(self):
        version=self.version(); self.product.name='Actuel'; self.product.save()
        before=snapshot_product(self.product)
        original=record_product
        def fail_final(*args,**kwargs):
            if kwargs.get('source')=='restore': raise RuntimeError('history final failure')
            return original(*args,**kwargs)
        with patch('historique.products.record_product',side_effect=fail_final):
            with self.assertRaises(RuntimeError): self.restore(version)
        self.assertEqual(snapshot_product(self.product),before)

    def test_permission_and_stale_confirmation(self):
        version=self.version(); digest=fingerprint(snapshot_product(self.product))
        stranger=get_user_model().objects.create_user('history-other')
        with self.assertRaises(PermissionDenied): self.restore(version,actor=stranger)
        self.product.name='Modification récente'; self.product.save()
        with self.assertRaises(HistoryConflict): self.restore(version,expected_fingerprint=digest)

    def test_signed_restore_receipt_applies_once_even_after_later_changes(self):
        version=self.version(); self.product.name='Modifié'; self.product.save()
        prepared=prepare_restore(self.manager,version)
        result=apply_restore(self.manager,prepared['token'])
        self.product.refresh_from_db(); self.product.name='Après restauration'; self.product.save()
        repeated=apply_restore(self.manager,prepared['token'])
        self.assertEqual(result.pk,repeated.pk)
        self.product.refresh_from_db(); self.assertEqual(self.product.name,'Après restauration')

    def test_editor_token_survives_sales_but_rejects_content_changes_and_other_user(self):
        token=editor_token(self.product,self.manager)
        Variant.objects.filter(pk=self.variant.pk).update(quantity=4,reserved=0,revision=8)
        validate_editor_token(self.product,self.manager,token)
        self.product.name='Autre nom'; self.product.save()
        with self.assertRaises(HistoryConflict): validate_editor_token(self.product,self.manager,token)
        with self.assertRaises(HistoryConflict): validate_editor_token(self.product,self.manager,'')

    def data(self):
        return {'name':'Polaire modifiée','price':'85.00','description':'Description complète',
            'category':self.category.pk, 'history_token':editor_token(self.product,self.manager),
            'photos-TOTAL_FORMS':'0','photos-INITIAL_FORMS':'0','colors-TOTAL_FORMS':'0','colors-INITIAL_FORMS':'0',
            'stock-TOTAL_FORMS':'0','stock-INITIAL_FORMS':'0'}

    def test_editor_records_before_and_after_and_stale_post_keeps_input(self):
        data=self.data()
        response=self.client.post(reverse('core:catalog_edit',args=[self.product.pk]),data)
        self.assertEqual(response.status_code,302)
        versions=HistoryVersion.objects.filter(scope='product',object_id=self.product.pk).order_by('sequence')
        self.assertEqual(versions.count(),2)
        self.assertEqual(versions[0].snapshot['fields']['name'],'Polaire conservée')
        self.assertEqual(versions[1].snapshot['fields']['name'],'Polaire modifiée')
        data['name']='Saisie ancienne à garder'
        response=self.client.post(reverse('core:catalog_edit',args=[self.product.pk]),data)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Saisie ancienne à garder')
        self.product.refresh_from_db(); self.assertEqual(self.product.name,'Polaire modifiée')
        self.assertEqual(versions.count(),2)

    def test_csv_quantity_only_preserves_override_price_and_does_not_version(self):
        rows=[{'ligne':2,'produit_id':self.product.pk,'variante_id':self.variant.pk,'revision':0,
            'taille':'M','couleur':'Vert','reference':'VERT-M','quantite':10}]
        process_inventory(rows,self.manager,apply=True)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.price,self.variant.quantity,self.variant.reserved),(Decimal('70.00'),10,2))
        self.assertFalse(HistoryVersion.objects.exists())

    def test_csv_reference_change_records_before_and_after(self):
        rows=[{'ligne':2,'produit_id':self.product.pk,'variante_id':self.variant.pk,'revision':0,
            'taille':'M','couleur':'Vert','reference':'NOUVELLE-REF','quantite':12}]
        process_inventory(rows,self.manager,apply=True)
        versions=HistoryVersion.objects.filter(object_id=self.product.pk).order_by('sequence')
        self.assertEqual(versions.count(),2)
        self.assertEqual(versions[0].snapshot['variants'][0]['reference'],'VERT-M')
        self.assertEqual(versions[1].snapshot['variants'][0]['reference'],'NOUVELLE-REF')

    def test_metadata_context_records_only_change_and_is_atomic(self):
        with product_change(self.product.pk,actor=self.manager) as product:
            product.description='Nouveau texte'; product.save(update_fields=['description'])
        self.assertEqual(HistoryVersion.objects.count(),2)
        with product_change(self.product.pk,actor=self.manager): pass
        self.assertEqual(HistoryVersion.objects.count(),2)
        with self.assertRaises(RuntimeError):
            with product_change(self.product.pk,actor=self.manager) as product:
                product.description='Ne pas garder'; product.save(update_fields=['description']); raise RuntimeError()
        self.product.refresh_from_db(); self.assertEqual(self.product.description,'Nouveau texte')

    def test_restore_inherited_price_observes_only_final_amount(self):
        self.variant.price=None; self.variant.save()
        version=self.version()
        self.product.price=Decimal('60'); self.product.save()
        self.variant.price=Decimal('90'); self.variant.save()
        last=PriceObservation.objects.latest('pk').pk
        self.restore(version)
        self.assertEqual(list(PriceObservation.objects.filter(variant=self.variant,pk__gt=last).values_list('price',flat=True)),[Decimal('80')])

    def test_restore_override_price_never_observes_transient_general_price(self):
        self.variant.price=Decimal('90'); self.variant.save()
        version=self.version()
        self.product.price=Decimal('100'); self.product.save()
        self.variant.price=None; self.variant.save()
        last=PriceObservation.objects.latest('pk').pk
        self.restore(version)
        self.assertEqual(list(PriceObservation.objects.filter(variant=self.variant,pk__gt=last).values_list('price',flat=True)),[Decimal('90')])

    def test_promotion_identity_targets_and_reference_prices_stay_intact(self):
        from datetime import timedelta
        from django.utils import timezone
        from core.models import ProductPromotion
        version=self.version()
        promotion=ProductPromotion.objects.create(product=self.product,starts_at=timezone.now(),
            ends_at=timezone.now()+timedelta(days=7),kind='percent',value=15,reference_confirmed=True,
            reference_prices={str(self.variant.pk):'70.00'},all_variants=False)
        promotion.targets.add(self.variant)
        self.product.price='100'; self.product.save()
        self.restore(version); promotion.refresh_from_db()
        self.assertEqual(promotion.value,15)
        self.assertEqual(promotion.reference_prices,{str(self.variant.pk):'70.00'})
        self.assertEqual(list(promotion.targets.values_list('pk',flat=True)),[self.variant.pk])
        self.assertTrue(promotion.enabled)

    def test_imported_static_gallery_becomes_frozen_archived_media(self):
        from django.templatetags.static import static
        with tempfile.TemporaryDirectory(prefix='history-static-') as root:
            target=Path(root,'archive-photo.webp'); target.write_bytes(b'static-original')
            self.product.include_imported_gallery=True; self.product.save()
            with override_settings(STATICFILES_DIRS=[root]), patch('historique.products.imported_gallery_urls',
                    side_effect=lambda product, initial: initial+[static('archive-photo.webp')] if product.include_imported_gallery else initial):
                version=self.version()
                target.unlink()
                self.restore(version)
                self.product.refresh_from_db()
                self.assertFalse(self.product.include_imported_gallery)
                image=self.product.images.order_by('-order').first()
                self.assertTrue(image.image.name.startswith('historique-assets/'))
                self.assertEqual(image.image.read(),b'static-original')

    def test_product_admin_form_rejects_stale_editor_and_saves_complete_history(self):
        import json
        from django.contrib.admin.sites import AdminSite
        from django.forms.models import model_to_dict
        from django.test import RequestFactory
        from core.admin import ProductAdmin
        admin_user=get_user_model().objects.create_superuser('history-admin','admin@example.test','unused')
        request=RequestFactory().post('/admin/core/product/'); request.user=admin_user
        admin=ProductAdmin(Product,AdminSite())
        form_class=admin.get_form(request,self.product)
        payload=model_to_dict(self.product)
        payload.pop('main_image',None)
        payload['translations']=json.dumps(payload['translations'])
        payload['history_token']=editor_token(self.product,admin_user)
        form=form_class(payload,instance=Product.objects.get(pk=self.product.pk))
        self.assertTrue(form.is_valid(),form.errors)
        changed=form.save(commit=False); changed.name='Modification admin'
        admin.save_model(request,changed,form,True)
        admin.save_related(request,form,[],True)
        self.assertEqual(HistoryVersion.objects.count(),2)
        form=form_class(payload,instance=Product.objects.get(pk=self.product.pk))
        self.assertFalse(form.is_valid())
        self.assertIn('changé',str(form.non_field_errors()))

    def test_reviewed_default_benefits_remain_frozen_when_source_copy_changes(self):
        import hashlib
        from django.utils.translation import override
        digest=hashlib.sha256(self.product.description.encode()).hexdigest()
        reviewed={self.product.slug:{'description_sha256':digest,'benefits':['soft']}}
        with patch('core.benefits.reviewed_catalog',return_value=reviewed), override('en'):
            version=self.version()
        self.assertIn('douce',version.snapshot['resolved_benefits'][0])
        reviewed[self.product.slug]['benefits']=['warm']
        with patch('core.benefits.reviewed_catalog',return_value=reviewed):
            self.restore(version)
        self.product.refresh_from_db()
        self.assertEqual(self.product.benefits,'\n'.join(version.snapshot['resolved_benefits']))
        self.assertIn('douce',self.product.benefit_lines[0])

    def test_main_static_photo_is_materialized_even_while_original_still_exists(self):
        with tempfile.TemporaryDirectory(prefix='history-main-static-') as root:
            path=Path(root,'main-static.webp'); path.write_bytes(b'kept-static-main')
            self.product.main_image=''; self.product.static_image='main-static.webp'; self.product.save()
            with override_settings(STATICFILES_DIRS=[root]):
                version=self.version()
                self.restore(version)
                self.product.refresh_from_db()
                self.assertEqual(self.product.static_image,'')
                self.assertTrue(self.product.main_image.name.startswith('historique-assets/'))
                path.unlink()
                self.assertEqual(self.product.main_image.read(),b'kept-static-main')
