import boutique.editorial_models
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def preserve_current_publications(apps, schema_editor):
    Page = apps.get_model('boutique', 'ContentPage')
    Version = apps.get_model('boutique', 'ContentPageVersion')
    for page in Page.objects.using(schema_editor.connection.alias).all().iterator():
        Version.objects.using(schema_editor.connection.alias).create(
            page_id=page.pk, publication_revision=page.revision, data=page.data,
            image=page.image.name or '', created_at=page.updated_at, created_by_id=page.updated_by_id)
        Page.objects.using(schema_editor.connection.alias).filter(pk=page.pk).update(published_revision=page.revision)


class Migration(migrations.Migration):
    dependencies = [
        ('boutique', '0006_editorial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name='contentpage', name='draft_data',
            field=models.JSONField(blank=True, default=None, null=True)),
        migrations.AddField(model_name='contentpage', name='draft_image',
            field=models.ImageField(blank=True, upload_to=boutique.editorial_models.editorial_image_path, verbose_name='Photo du brouillon')),
        migrations.AddField(model_name='contentpage', name='published_revision',
            field=models.PositiveIntegerField(default=0, editable=False)),
        migrations.CreateModel(
            name='ContentPageVersion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('publication_revision', models.PositiveIntegerField(editable=False)),
                ('data', models.JSONField(blank=True, default=dict, editable=False)),
                ('image', models.ImageField(blank=True, editable=False, upload_to=boutique.editorial_models.editorial_image_path)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('created_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('page', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='boutique.contentpage')),
            ],
            options={
                'verbose_name': 'Version publiée d’une page',
                'verbose_name_plural': 'Versions publiées des pages',
                'ordering': ['-publication_revision', '-pk'],
                'constraints': [models.UniqueConstraint(fields=('page', 'publication_revision'), name='editorial_page_version_unique')],
            },
        ),
        migrations.RunPython(preserve_current_publications, migrations.RunPython.noop),
        migrations.AddConstraint(model_name='contentpage',
            constraint=models.CheckConstraint(condition=models.Q(published_revision__lte=models.F('revision')), name='editorial_page_public_revision_valid')),
    ]
