from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accueil', '0002_hero_slide')]

    operations = [
        migrations.AddField(
            model_name='heroslide', name='section',
            field=models.CharField(choices=[('hero', 'Grande photo d’accueil'),
                                            ('feature', 'Invitation au grand air')],
                                   default='hero', max_length=10, verbose_name='Bloc de l’accueil')),
        migrations.AddConstraint(
            model_name='heroslide',
            constraint=models.CheckConstraint(condition=models.Q(section__in=['hero', 'feature']),
                                              name='homepage_slide_valid_section')),
    ]
