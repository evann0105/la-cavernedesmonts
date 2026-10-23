from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('retours', '0006_return_declarant')]

    operations = [migrations.AddField(
        model_name='returnmail', name='superseded_at',
        field=models.DateTimeField(blank=True, editable=False, null=True),
    )]
