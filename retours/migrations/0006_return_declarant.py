from django.db import migrations
from config.encrypted_fields import EncryptedJSONField


class Migration(migrations.Migration):
    dependencies = [('retours', '0005_refund_price_snapshot')]
    operations = [migrations.AddField(
        model_name='returnrequest',
        name='declarant',
        field=EncryptedJSONField(blank=True, default=dict),
    )]
