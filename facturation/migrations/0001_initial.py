import config.encrypted_fields
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [('paiement', '0008_order_contract')]
    operations = [
        migrations.CreateModel(
            name='InvoiceSequence',
            fields=[
                ('year', models.PositiveSmallIntegerField(primary_key=True, serialize=False)),
                ('next_number', models.PositiveBigIntegerField(default=1)),
            ],
            options={
                'verbose_name': 'Séquence annuelle des factures web',
                'constraints': [models.CheckConstraint(condition=models.Q(next_number__gte=1), name='invoice_sequence_positive')],
            },
        ),
        migrations.CreateModel(
            name='Invoice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.CharField(editable=False, max_length=40, unique=True, verbose_name='Numéro de facture')),
                ('issued_at', models.DateTimeField(editable=False, verbose_name='Date d’émission')),
                ('snapshot', config.encrypted_fields.EncryptedJSONField(editable=False)),
                ('issued_by_identifier', models.CharField(blank=True, editable=False, max_length=128)),
                ('order', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='invoice', to='paiement.order')),
            ],
            options={'ordering': ['-issued_at', '-pk'], 'verbose_name': 'Facture émise', 'verbose_name_plural': 'Factures émises'},
        ),
    ]
