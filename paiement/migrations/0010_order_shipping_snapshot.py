from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('paiement', '0009_order_billing')]
    operations = [migrations.AddField(model_name='order', name='shipping_snapshot',
                  field=models.JSONField(default=dict, blank=True, editable=False))]
