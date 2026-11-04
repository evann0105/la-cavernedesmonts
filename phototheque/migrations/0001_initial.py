from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='Folder', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('name', models.CharField(max_length=80, verbose_name='Nom du dossier')),
            ('name_key', models.CharField(editable=False, max_length=240, unique=True)),
                ('system_key', models.CharField(blank=True, editable=False, max_length=16, null=True, unique=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
        ], options={'ordering': ['name_key', 'pk']}),
        migrations.CreateModel(name='Photo', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('image', models.ImageField(editable=False, max_length=255, upload_to='phototheque/images/')),
            ('thumbnail', models.ImageField(editable=False, max_length=255, upload_to='phototheque/thumbnails/')),
            ('digest', models.CharField(editable=False, max_length=64, unique=True)),
            ('source_digest', models.CharField(db_index=True, editable=False, max_length=64)),
            ('title', models.CharField(max_length=160, verbose_name='Nom de la photo')),
            ('original_name', models.CharField(editable=False, max_length=255)),
            ('width', models.PositiveIntegerField(editable=False)),
            ('height', models.PositiveIntegerField(editable=False)),
            ('size', models.PositiveIntegerField(editable=False)),
            ('archived_at', models.DateTimeField(blank=True, editable=False, null=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('source_label', models.CharField(blank=True, editable=False, max_length=160)),
            ('folder', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='photos', to='phototheque.folder')),
            ('uploaded_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
        ], options={'ordering': ['-created_at', '-pk'],
                    'indexes': [models.Index(fields=['archived_at', 'folder', '-created_at'], name='photo_library_listing')],
                    'constraints': [models.CheckConstraint(condition=models.Q(width__gt=0, height__gt=0, size__gt=0), name='photo_positive_dimensions_size')]}),
    ]
