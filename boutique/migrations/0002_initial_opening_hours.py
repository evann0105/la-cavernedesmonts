from datetime import time
from django.db import migrations


def seed_opening_hours(apps, schema_editor):
    OpeningHours = apps.get_model('boutique', 'OpeningHours')
    OpeningDay = apps.get_model('boutique', 'OpeningDay')
    database = schema_editor.connection.alias
    schedule, created = OpeningHours.objects.using(database).get_or_create(
        pk=1,
        defaults={'notice': 'Ouvert tous les jours en haute saison. Appelez-nous pour confirmer les horaires avant votre visite.'},
    )
    if created:
        OpeningDay.objects.using(database).bulk_create([
            OpeningDay(schedule=schedule, weekday=weekday, is_closed=weekday < 2,
                       opens_at=time(9, 30) if weekday >= 2 else None,
                       closes_at=time(12) if weekday >= 2 else None,
                       reopens_at=time(14, 30) if 2 <= weekday <= 5 else None,
                       recloses_at=time(18, 30) if 2 <= weekday <= 5 else None)
            for weekday in range(7)
        ])


class Migration(migrations.Migration):
    dependencies = [('boutique', '0001_initial')]
    operations = [migrations.RunPython(seed_opening_hours, migrations.RunPython.noop)]
