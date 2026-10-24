from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('boutique', '0007_editorial_drafts_and_versions')]

    operations = [
        migrations.AddField(model_name='siteannouncement', name='archived_at',
            field=models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Date d’archivage')),
        migrations.AddConstraint(model_name='siteannouncement',
            constraint=models.CheckConstraint(condition=models.Q(archived_at__isnull=True) | models.Q(enabled=False),
                                              name='editorial_announcement_archive_inactive')),
    ]
