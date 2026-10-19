from decimal import Decimal
from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('stocks', '0003_variantlabelcorrection')]
    operations = [
        migrations.AddField(model_name='variant', name='price', field=models.DecimalField('Prix de cette taille et couleur (€)', max_digits=10, decimal_places=2, null=True, blank=True, default=None, validators=[MinValueValidator(Decimal('0.01'))])),
        migrations.AddConstraint(model_name='variant', constraint=models.CheckConstraint(condition=models.Q(price__isnull=True) | models.Q(price__gt=0), name='positive_variant_price')),
    ]
