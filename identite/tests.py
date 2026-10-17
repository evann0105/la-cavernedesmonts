from pathlib import Path
from tempfile import TemporaryDirectory
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings, RequestFactory
from django.db import connection
from django.contrib.auth import authenticate
from config.encrypted_fields import decrypt, encrypt
from .models import User
from espace.models import Address
from paiement.models import Order


class EncryptionTests(TestCase):
    def test_account_is_encrypted_in_sql_and_login_by_email_works(self):
        user=User.objects.create_user('encrypted-user',email='secret@example.test',first_name='Camille',last_name='Montagne',password='Private-password-123')
        with connection.cursor() as cursor:
            cursor.execute('SELECT email, first_name, last_name, password FROM identite_user WHERE id=%s',[user.pk])
            values=cursor.fetchone()
        self.assertTrue(all(v.startswith('enc:v1:') for v in values[:3]))
        self.assertNotIn('Private-password-123', values[3])
        self.assertEqual(authenticate(request=RequestFactory().post('/accounts/login/'),username='SECRET@example.test',password='Private-password-123'),user)
        self.assertEqual(User.objects.get(email__iexact='SECRET@example.test').pk,user.pk)

    def test_email_updates_keep_index_in_sync(self):
        user=User.objects.create_user('indexed',email='old@example.test')
        user.email='new@example.test';user.save(update_fields=['email'])
        self.assertEqual(User.objects.get(email__iexact='NEW@example.test').pk,user.pk)
        self.assertFalse(User.objects.filter(email__iexact='old@example.test').exists())
        User.objects.filter(pk=user.pk).update(email='third@example.test')
        self.assertTrue(User.objects.filter(email__iexact='third@example.test').exists())

    def test_personal_address_and_shipping_are_encrypted_and_round_trip(self):
        user=User.objects.create_user('address-user')
        address=Address.objects.create(user=user,recipient='Camille',line1='Rue privée',postal_code='01410',city='Lélex')
        order=Order.objects.create(user=user,session_key='test',items=[],subtotal=1,customer_email='private@example.test',shipping_details={'address':{'city':'Lélex'}})
        with connection.cursor() as cursor:
            cursor.execute('SELECT line1 FROM espace_address WHERE id=%s',[address.pk]);self.assertTrue(cursor.fetchone()[0].startswith('enc:v1:'))
            cursor.execute('SELECT customer_email,shipping_details FROM paiement_order');self.assertTrue(all(v.startswith('enc:v1:') for v in cursor.fetchone()))
        address.refresh_from_db();order.refresh_from_db()
        self.assertEqual(address.line1,'Rue privée')
        self.assertEqual(order.shipping_details,{'address':{'city':'Lélex'}})

    def test_ciphertext_is_randomized_and_fails_closed(self):
        self.assertNotEqual(encrypt('Camille'),encrypt('Camille'))
        with self.assertRaises(ValueError):decrypt('Camille')
        encrypted=encrypt('Camille')
        with TemporaryDirectory() as folder:
            path=Path(folder)/'key';path.write_bytes(Fernet.generate_key())
            with override_settings(DATA_ENCRYPTION_KEY_FILE=str(path)):
                with self.assertRaises(ValueError):decrypt(encrypted)
        with self.assertRaises(ValueError):decrypt(encrypted[:-5]+'AAAAA')
