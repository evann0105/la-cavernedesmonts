from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core', '0017_sale_campaign')]
    operations = [
        migrations.AddField(
            model_name='product',
            name='duplicate_of',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='duplicate_records', to='core.product', verbose_name='Fiche conservée après vérification du doublon'),
        ),
        migrations.AddConstraint(
            model_name='product',
            constraint=models.CheckConstraint(condition=~models.Q(duplicate_of=models.F('pk')), name='product_duplicate_not_self'),
        ),
    ]
