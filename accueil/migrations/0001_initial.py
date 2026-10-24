import accueil.models
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ('core', '0018_product_duplicate_of'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='HomepageConfiguration',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('data', models.JSONField(blank=True, default=dict)),
                ('hero_image', models.ImageField(blank=True, upload_to=accueil.models.homepage_image_path, verbose_name='Photo principale')),
                ('hero_mobile_image', models.ImageField(blank=True, upload_to=accueil.models.homepage_image_path, verbose_name='Photo pour téléphone')),
                ('feature_image', models.ImageField(blank=True, upload_to=accueil.models.homepage_image_path, verbose_name='Photo du grand air')),
                ('family_accessories_image', models.ImageField(blank=True, upload_to=accueil.models.homepage_image_path, verbose_name='Photo accessoires bébé')),
                ('family_fleece_image', models.ImageField(blank=True, upload_to=accueil.models.homepage_image_path, verbose_name='Photo polaires bébé')),
                ('family_gifts_image', models.ImageField(blank=True, upload_to=accueil.models.homepage_image_path, verbose_name='Photo petites attentions')),
                ('store_image', models.ImageField(blank=True, upload_to=accueil.models.homepage_image_path, verbose_name='Photo du magasin')),
                ('revision', models.PositiveIntegerField(default=1, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, editable=False, null=True,
                    on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'Personnalisation de l’accueil',
                     'verbose_name_plural': 'Personnalisation de l’accueil'},
        ),
        migrations.CreateModel(
            name='FeaturedHomepageProduct',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('position', models.PositiveSmallIntegerField(unique=True,
                    validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(4)])),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name='homepage_featured_positions', to='core.product')),
            ],
            options={'ordering': ['position', 'pk'], 'verbose_name': 'Produit mis en avant sur l’accueil',
                     'verbose_name_plural': 'Produits mis en avant sur l’accueil'},
        ),
        migrations.AddConstraint(model_name='homepageconfiguration', constraint=models.CheckConstraint(
            condition=models.Q(id=1), name='one_homepage_configuration')),
        migrations.AddConstraint(model_name='homepageconfiguration', constraint=models.CheckConstraint(
            condition=models.Q(revision__gte=1), name='homepage_positive_revision')),
        migrations.AddConstraint(model_name='featuredhomepageproduct', constraint=models.UniqueConstraint(
            fields=('product',), name='homepage_featured_unique_product')),
        migrations.AddConstraint(model_name='featuredhomepageproduct', constraint=models.CheckConstraint(
            condition=models.Q(position__gte=1, position__lte=4), name='homepage_featured_position_range')),
    ]
