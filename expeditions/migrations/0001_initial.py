import config.encrypted_fields
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ('paiement', '0010_order_shipping_snapshot'),
        ('retours', '0007_mail_superseded_at'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='Fulfillment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('state', models.CharField(choices=[('preparing', 'En préparation'), ('ready', 'Prête à expédier'), ('shipped', 'Expédiée')], default='preparing', max_length=12, verbose_name='État de préparation')),
                ('revision', models.PositiveIntegerField(default=1, editable=False)),
                ('ready_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('shipped_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('carrier', models.CharField(choices=[('colissimo', 'Colissimo / La Poste'), ('other', 'Autre transporteur')], default='colissimo', max_length=12, verbose_name='Transporteur')),
                ('carrier_name', models.CharField(blank=True, max_length=150, verbose_name='Nom de l’autre transporteur')),
                ('tracking_number', config.encrypted_fields.EncryptedTextField(blank=True, default='', validators=[django.core.validators.MaxLengthValidator(120)], verbose_name='Numéro de suivi')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('order', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='fulfillment', to='paiement.order')),
                ('updated_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'Préparation de commande', 'verbose_name_plural': 'Préparations de commandes'},
        ),
        migrations.CreateModel(
            name='FulfillmentEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(max_length=32)),
                ('from_state', models.CharField(max_length=12)),
                ('to_state', models.CharField(max_length=12)),
                ('label', models.CharField(max_length=200)),
                ('note', config.encrypted_fields.EncryptedTextField(blank=True, default='', validators=[django.core.validators.MaxLengthValidator(3000)], verbose_name='Note interne')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('fulfillment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='events', to='expeditions.fulfillment')),
            ],
            options={'ordering': ['created_at', 'pk'], 'verbose_name': 'Événement de préparation', 'verbose_name_plural': 'Historique de préparation'},
        ),
        migrations.AddConstraint(
            model_name='fulfillment',
            constraint=models.CheckConstraint(condition=models.Q(revision__gte=1), name='fulfillment_positive_revision'),
        ),
        migrations.AddConstraint(
            model_name='fulfillment',
            constraint=models.CheckConstraint(condition=models.Q(carrier__in=['colissimo', 'other']), name='fulfillment_known_carrier'),
        ),
        migrations.AddConstraint(
            model_name='fulfillment',
            constraint=models.CheckConstraint(condition=(
                models.Q(state='preparing', ready_at__isnull=True, shipped_at__isnull=True)
                | models.Q(state='ready', ready_at__isnull=False, shipped_at__isnull=True)
                | models.Q(state='shipped', ready_at__isnull=False, shipped_at__isnull=False, shipped_at__gte=models.F('ready_at'))
            ), name='fulfillment_consistent_state_dates'),
        ),
    ]
