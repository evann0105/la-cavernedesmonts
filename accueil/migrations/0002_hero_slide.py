import accueil.models
import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accueil', '0001_initial')]
    operations = [
        migrations.CreateModel(
            name='HeroSlide',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to=accueil.models.homepage_image_path, verbose_name='Photo supplémentaire')),
                ('mobile_image', models.ImageField(blank=True, upload_to=accueil.models.homepage_image_path, verbose_name='Cadrage pour téléphone')),
                ('alt', models.CharField(max_length=200, verbose_name='Description de la photo')),
                ('position', models.PositiveSmallIntegerField(
                    validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(5)],
                    verbose_name='Ordre après la photo principale')),
                ('configuration', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name='hero_slides', to='accueil.homepageconfiguration')),
            ],
            options={'ordering': ['position', 'pk'], 'verbose_name': 'Photo du diaporama d’accueil',
                     'verbose_name_plural': 'Photos du diaporama d’accueil'},
        ),
        migrations.AddConstraint(model_name='heroslide', constraint=models.CheckConstraint(
            condition=models.Q(configuration_id=1), name='hero_slide_homepage_configuration')),
        migrations.AddConstraint(model_name='heroslide', constraint=models.CheckConstraint(
            condition=models.Q(position__gte=1, position__lte=5), name='hero_slide_position_range')),
    ]
