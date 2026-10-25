from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0020_category_archival'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='CatalogChange',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('bulk', 'Modification groupée'), ('undo', 'Annulation')], max_length=10)),
                ('actor_label', models.CharField(max_length=150)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('items', models.JSONField(default=list)),
                ('undone_at', models.DateTimeField(blank=True, null=True)),
                ('undone_by_label', models.CharField(blank=True, max_length=150)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='catalogue_changes', to=settings.AUTH_USER_MODEL)),
                ('undone_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='catalogue_undo_actions', to=settings.AUTH_USER_MODEL)),
                ('reverses', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='undo_operation', to='core.catalogchange')),
            ],
            options={'ordering': ['-created_at', '-pk'],
                     'constraints': [models.CheckConstraint(condition=models.Q(('kind__in', ['bulk', 'undo'])), name='catalog_change_valid_kind')]},
        ),
    ]
