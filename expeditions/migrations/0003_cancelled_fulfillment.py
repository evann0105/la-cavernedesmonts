from django.db import migrations, models
from django.db.models import F, Q


class Migration(migrations.Migration):
    dependencies = [('expeditions', '0002_contact_corrections')]
    operations = [
        migrations.AlterField(model_name='fulfillment', name='state', field=models.CharField(
            'État de préparation', max_length=12, default='preparing', choices=[
                ('preparing', 'En préparation'), ('ready', 'Prête à expédier'),
                ('shipped', 'Expédiée'), ('cancelled', 'Annulée')])),
        migrations.RemoveConstraint(model_name='fulfillment', name='fulfillment_consistent_state_dates'),
        migrations.AddConstraint(model_name='fulfillment', constraint=models.CheckConstraint(
            condition=(Q(state='cancelled', shipped_at__isnull=True)
                | Q(state='preparing', ready_at__isnull=True, shipped_at__isnull=True)
                | Q(state='ready', ready_at__isnull=False, shipped_at__isnull=True)
                | Q(state='shipped', ready_at__isnull=False, shipped_at__isnull=False,
                    shipped_at__gte=F('ready_at'))), name='fulfillment_consistent_state_dates')),
    ]
