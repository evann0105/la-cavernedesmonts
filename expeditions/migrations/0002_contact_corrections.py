import config.encrypted_fields
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('expeditions', '0001_initial')]

    operations = [
        migrations.AddField(
            model_name='fulfillment', name='delivery_address_override',
            field=config.encrypted_fields.EncryptedJSONField(
                blank=True, default=dict, verbose_name='Adresse de livraison corrigée'),
        ),
        migrations.AddField(
            model_name='fulfillment', name='notification_email_override',
            field=config.encrypted_fields.EncryptedEmailField(
                blank=True, default='', verbose_name='E-mail de notification corrigé'),
        ),
        migrations.AddField(
            model_name='fulfillmentevent', name='contact_snapshot',
            field=config.encrypted_fields.EncryptedJSONField(
                blank=True, default=dict, verbose_name='Historique des coordonnées'),
        ),
    ]
