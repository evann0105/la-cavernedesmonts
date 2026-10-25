from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0019_category_management')]
    operations = [
        migrations.AddField(model_name='category', name='archived_at',
            field=models.DateTimeField('Archivée le', blank=True, editable=False, null=True)),
        migrations.AddConstraint(model_name='category',
            constraint=models.CheckConstraint(
                condition=models.Q(archived_at__isnull=True) | models.Q(show_in_menu=False),
                name='archived_category_hidden_menu')),
    ]
