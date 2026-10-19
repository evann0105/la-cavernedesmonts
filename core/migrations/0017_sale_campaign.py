from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core', '0016_product_promotions')]
    operations = [
        migrations.CreateModel(name='SaleCampaign', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('title', models.CharField(max_length=160, verbose_name='Nom de l’opération')),
            ('criteria', models.JSONField(blank=True, default=dict, verbose_name='Critères de sélection')),
            ('revision', models.PositiveIntegerField(default=0)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
        ], options={'ordering': ['-created_at', '-pk']}),
        migrations.AddField(model_name='productpromotion', name='campaign', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='promotions', to='core.salecampaign')),
    ]
