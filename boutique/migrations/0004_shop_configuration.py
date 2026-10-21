import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('boutique', '0003_openingperiod_openingperiodday_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='ShopConfiguration',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('phone', models.CharField(default='04 50 40 46 17', max_length=64, verbose_name='Téléphone de la boutique')),
                ('email', models.EmailField(default='contact@la-cavernedesmonts.fr', max_length=254, verbose_name='Adresse e-mail publique')),
                ('store_address_line1', models.CharField(default='225 rue des Monts-Jura', max_length=255, verbose_name='Numéro et rue')),
                ('store_address_line2', models.CharField(blank=True, default='Résidence Les Gentianes', max_length=255, verbose_name='Complément d’adresse')),
                ('store_postal_code', models.CharField(default='01410', max_length=20, verbose_name='Code postal')),
                ('store_city', models.CharField(default='Lélex', max_length=120, verbose_name='Ville')),
                ('store_country', models.CharField(default='France', max_length=80, verbose_name='Pays')),
                ('business_name', models.CharField(default='Élodie FOLOPPE', max_length=255, verbose_name='Nom juridique de l’entreprise')),
                ('legal_form', models.CharField(default='EI', max_length=80, verbose_name='Forme juridique')),
                ('siren', models.CharField(default='525 114 914', max_length=20, verbose_name='SIREN')),
                ('siret', models.CharField(default='525 114 914 00019', max_length=25, verbose_name='SIRET')),
                ('registration', models.CharField(default='525 114 914 RCS Bourg-en-Bresse', max_length=255, verbose_name='Immatriculation')),
                ('publisher', models.CharField(blank=True, default='', max_length=255, verbose_name='Responsable de publication')),
                ('business_address', models.TextField(blank=True, default='Résidence Les Gentianes, 225 rue des Monts-Jura, 01410 Lélex, France', max_length=2000, verbose_name='Adresse professionnelle distincte')),
                ('business_same_as_store', models.BooleanField(default=False, verbose_name='L’adresse professionnelle est celle du magasin')),
                ('return_address', models.TextField(blank=True, default='La Caverne des Monts\n225 Rue des Monts Jura\n01410 Lélex', max_length=2000, verbose_name='Adresse de retour distincte')),
                ('returns_same_as_store', models.BooleanField(default=False, verbose_name='Utiliser la même adresse que le magasin pour les retours')),
                ('revision', models.PositiveIntegerField(default=1, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Réglages de la boutique',
                'verbose_name_plural': 'Réglages de la boutique',
                'constraints': [
                    models.CheckConstraint(condition=models.Q(id=1), name='one_shop_configuration'),
                    models.CheckConstraint(condition=models.Q(revision__gte=1), name='shop_configuration_positive_revision'),
                ],
            },
        ),
    ]
