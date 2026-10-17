from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('paiement','0007_order_paid_line_totals')]
    operations = [
        migrations.AddField(model_name='order',name='terms_snapshot',field=models.JSONField(default=dict,blank=True,editable=False)),
        migrations.AddField(model_name='order',name='terms_accepted_at',field=models.DateTimeField(null=True,blank=True,editable=False)),
    ]
