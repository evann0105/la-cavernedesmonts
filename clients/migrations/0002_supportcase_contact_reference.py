from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('clients', '0001_initial')]

    operations = [
        migrations.AddField(
            model_name='supportcase',
            name='contact_reference',
            field=models.UUIDField(blank=True, db_index=True, editable=False, null=True),
        ),
    ]
