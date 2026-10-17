import config.encrypted_fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('paiement', '0008_order_contract')]
    operations = [
        migrations.AddField(model_name='order', name='billing_details', field=config.encrypted_fields.EncryptedJSONField(blank=True, default=dict)),
        migrations.AddField(model_name='order', name='invoice_profile', field=models.JSONField(blank=True, default=dict, editable=False)),
    ]
