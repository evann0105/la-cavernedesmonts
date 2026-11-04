from historique.products import snapshot_product, record_change, record_product
"""Small, repeatable fixtures for the separate payment playground only."""
from datetime import timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import secrets
import stat

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from avoirs.models import CustomerBenefit
from boutique.models import (
    ShippingConfiguration, ShippingCountry, ShippingZone, ShopConfiguration,
)
from connexion.models import EmailVerification
from core.models import Category, Product
from espace.models import Address
from paiement.test_mode import assert_isolated_test_environment
from stocks.models import Variant


ACCOUNTS = {
    'admin': {'username': 'evann-test', 'email': 'vendeuse@example.test'},
    'client': {'username': 'client-test', 'email': 'client-test@example.test'},
}
PRODUCTS = (
    ('polaire', 'Polaire fictive — essai à 80 €', '80.00', 'polaires',
     'core/img/femmes/femme-736-veste-polaire-udilas-canadian-peak/00.jpg',
     (('M', 'Rouge'), ('L', 'Rouge'), ('M', 'Vert'), ('L', 'Vert'))),
    ('haut', 'Haut fictif — essai à 35 €', '35.00', 'hauts',
     'core/img/hommes/homme-930-sweat-homme-a-fermeture-marine-et-camel/00.jpg',
     (('M', 'Bleu'),)),
    ('veste', 'Veste fictive — essai à 110 €', '110.00', 'vestes',
     'core/img/hommes/homme-990-veste-technique-homme-impermeable-en-softshell/00.jpg',
     (('M', 'Noir'), ('L', 'Noir'))),
)


def account_credentials():
    """Keep generated passwords outside the repository and out of command output."""
    directory = Path(settings.PAYMENT_TEST_DIRECTORY)
    if not directory.is_absolute() or not directory.is_dir() or directory.is_symlink():
        raise CommandError('Le dossier privé des paiements fictifs doit être préparé avec ./test-payments.')
    path = directory / 'accounts.json'
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise CommandError('Le fichier accounts.json doit être un fichier privé avec des droits 600.')
            credentials = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise CommandError('Le fichier privé accounts.json est illisible.') from error
    except OSError as error:
        raise CommandError('Impossible de créer le fichier privé des comptes de test.') from error
    else:
        credentials = {
            role: {**identity, 'password': secrets.token_urlsafe(24)}
            for role, identity in ACCOUNTS.items()
        }
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(credentials, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    for role, identity in ACCOUNTS.items():
        account = credentials.get(role, {}) if isinstance(credentials, dict) else {}
        if (not isinstance(account, dict)
                or any(account.get(key) != value for key, value in identity.items())
                or not isinstance(account.get('password'), str)
                or len(account['password']) < 16):
            raise CommandError('Le fichier privé accounts.json ne correspond pas aux comptes fictifs attendus.')
    return path, credentials


class Command(BaseCommand):
    help = 'Prépare uniquement la boutique séparée de paiements fictifs, sans réinitialiser les essais précédents.'

    def handle(self, *args, **options):
        if not assert_isolated_test_environment(CommandError):
            raise CommandError('Cette commande est réservée à ./test-payments et à sa base séparée.')
        credentials_path, credentials = account_credentials()
        with transaction.atomic():
            accounts = self.seed_accounts(credentials)
            self.seed_products()
            self.seed_shop(accounts['admin'])
            self.seed_benefits(accounts['admin'], accounts['client'])
        self.stdout.write(self.style.SUCCESS('La boutique de paiements fictifs est prête.'))
        self.stdout.write('Comptes : evann-test (administration) et client-test (client).')
        self.stdout.write(f'Identifiants privés : {credentials_path}')
        self.stdout.write('Catalogue initial : 3 articles à 80 €, 35 € et 110 €, 7 déclinaisons avec 20 unités chacune.')
        self.stdout.write('Livraison initiale : France, Colissimo, 6,90 €, offerte dès 99 €. Aucun colis réel.')
        self.stdout.write('Codes personnels pour client-test : TEST15 (15 % dès 70 €) et TEST10 (avoir de 10 €).')
        self.stdout.write('Chaque code est utilisable une fois. Les essais et modifications existants sont conservés.')
        self.stdout.write('Aucun e-mail réel envoyé ; aucune ouverture des ventes réelles validée.')

    def seed_accounts(self, credentials):
        users = {}
        for role, identity in ACCOUNTS.items():
            user, created = get_user_model().objects.get_or_create(
                username=identity['username'],
                defaults={
                    'email': identity['email'], 'first_name': 'Compte fictif',
                    'last_name': 'Vendeuse' if role == 'admin' else 'Client',
                    'password': make_password(credentials[role]['password']),
                    'is_staff': role == 'admin', 'is_superuser': role == 'admin',
                },
            )
            if user.email.lower() != identity['email']:
                raise CommandError('Un nom de compte fictif est déjà utilisé avec une autre adresse. Aucun compte modifié.')
            if created:
                EmailVerification.objects.create(user=user, verified_email=user.email, verified_at=timezone.now())
            users[role] = user
        client = users['client']
        if not Address.objects.filter(user=client).exists():
            Address.objects.create(user=client, label='Adresse fictive', recipient='Client Fictif',
                                   line1='2 rue des Essais', postal_code='75001', city='Paris', country='FR')
        return users

    def seed_products(self):
        category, _ = Category.objects.get_or_create(slug='essais-paiement', defaults={
            'name': 'Essais de paiement', 'menu_label': 'Essais de paiement',
            'show_in_menu': True, 'position': 0,
        })
        for key, name, price, article_type, image, combinations in PRODUCTS:
            reference = f'FICTIF-{key.upper()}'
            product, created = Product.objects.select_for_update().get_or_create(slug=f'essai-paiement-{key}', defaults={
                'name': name, 'price': Decimal(price), 'category': category,
                'description': 'Article fictif réservé aux essais de paiement. Photo illustrative, aucun article expédié.',
                'brand': 'MARQUE FICTIVE', 'model_reference': reference,
                'benefits': 'Permet de tester le paiement sans argent réel.',
                'sizes': ', '.join(dict.fromkeys(size for size, _ in combinations)),
                'main_color': combinations[0][1], 'static_image': image,
                'article_type': article_type, 'is_featured': True,
                'is_published': True, 'availability': 'available',
                'include_imported_gallery': False,
            })
            before = None if created else snapshot_product(product)
            if product.model_reference != reference:
                raise CommandError('Une référence de produit fictif est déjà utilisée par un autre article.')
            for size, color in combinations:
                Variant.objects.get_or_create(product=product, size=size, color=color, defaults={
                    'quantity': 20, 'reference': f'{reference}-{size}-{color.upper()}',
                })

            record_change(product, before, label='Article fictif de paiement préparé')

    def seed_shop(self, admin):
        ShopConfiguration.objects.get_or_create(pk=1, defaults={
            'business_name': 'BOUTIQUE FICTIVE — ESSAIS', 'legal_form': 'EI',
            'siren': '000000000', 'siret': '00000000000000',
            'registration': 'DONNÉES FICTIVES — AUCUNE IMMATRICULATION',
            'publisher': 'Responsable fictif', 'phone': '01 00 00 00 00',
            'email': 'boutique@example.test', 'store_address_line1': '1 rue des Essais',
            'store_address_line2': '', 'store_postal_code': '75001',
            'store_city': 'Paris', 'store_country': 'France',
            'business_address': '1 rue des Essais, 75001 Paris, France',
            'business_same_as_store': True,
            'return_address': 'BOUTIQUE FICTIVE — ESSAIS\n1 rue des Essais\n75001 Paris, France',
            'returns_same_as_store': False, 'updated_by': admin,
        })
        policy, _ = ShippingConfiguration.objects.get_or_create(pk=1, defaults={
            'enabled': True, 'free_shipping_enabled': True,
            'free_shipping_threshold': Decimal('99.00'),
            'preparation_days_min': 1, 'preparation_days_max': 2,
            'carrier_name': 'Colissimo', 'service_name': 'Colissimo — essai fictif',
            'carrier_website': 'https://www.laposte.fr/',
            'public_notice': 'Simulation uniquement : aucun colis ne sera expédié.', 'updated_by': admin,
        })
        # Preserve merchant edits and the zone chosen for France after the first seed.
        if not ShippingCountry.objects.filter(code='FR').exists():
            zone = ShippingZone.objects.create(configuration=policy, title='France — essais fictifs',
                price=Decimal('6.90'), free_shipping_eligible=True, delivery_days_min=2,
                delivery_days_max=3, active=True, updated_by=admin)
            ShippingCountry.objects.create(zone=zone, code='FR')

    def seed_benefits(self, admin, client):
        now = timezone.now()
        for code, kind, value, minimum, label in (
            ('TEST15', 'percent', '15.00', '70.00', 'Essai : 15 % dès 70 €'),
            ('TEST10', 'credit', '10.00', '0.00', 'Essai : avoir de 10 €'),
        ):
            benefit, _ = CustomerBenefit.objects.get_or_create(code=code, defaults={
                'recipient_email': client.email, 'user': client, 'bound_user_id': client.pk,
                'label': label, 'justification': 'Bon fictif pour tester une réduction lors du paiement. Sans valeur réelle.',
                'kind': kind, 'value': Decimal(value), 'minimum_subtotal': Decimal(minimum),
                'starts_at': now - timedelta(days=1), 'ends_at': now + timedelta(days=365),
                'created_by': admin,
            })
            if benefit.bound_user_id != client.pk or benefit.recipient_email.lower() != client.email.lower():
                raise CommandError('Un code d’essai appartient déjà à un autre compte. Aucun code modifié.')
