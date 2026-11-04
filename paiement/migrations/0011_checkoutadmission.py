import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('paiement', '0010_order_shipping_snapshot')]
    operations = [migrations.CreateModel(
        name='CheckoutAdmission',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('ip_hash', models.CharField(db_index=True, max_length=64)),
            ('session_hash', models.CharField(db_index=True, max_length=64)),
            ('account_hash', models.CharField(blank=True, db_index=True, max_length=64)),
            ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ('order', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='checkout_admission', to='paiement.order')),
        ],
    )]
