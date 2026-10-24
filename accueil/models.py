"""Editable home content; catalogue products retain their own publication rules."""
from uuid import uuid4

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


def homepage_image_path(instance, filename):
    # Uploads are decoded and re-encoded by ShopImageField before reaching here.
    return f'homepage/{uuid4().hex}.webp'


class HomepageConfiguration(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    data = models.JSONField(default=dict, blank=True)
    hero_image = models.ImageField('Photo principale', upload_to=homepage_image_path, blank=True)
    hero_mobile_image = models.ImageField('Photo pour téléphone', upload_to=homepage_image_path, blank=True)
    feature_image = models.ImageField('Photo du grand air', upload_to=homepage_image_path, blank=True)
    family_accessories_image = models.ImageField('Photo accessoires bébé', upload_to=homepage_image_path, blank=True)
    family_fleece_image = models.ImageField('Photo polaires bébé', upload_to=homepage_image_path, blank=True)
    family_gifts_image = models.ImageField('Photo petites attentions', upload_to=homepage_image_path, blank=True)
    store_image = models.ImageField('Photo du magasin', upload_to=homepage_image_path, blank=True)
    revision = models.PositiveIntegerField(default=1, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False)

    class Meta:
        verbose_name = 'Personnalisation de l’accueil'
        verbose_name_plural = 'Personnalisation de l’accueil'
        constraints = [
            models.CheckConstraint(condition=Q(id=1), name='one_homepage_configuration'),
            models.CheckConstraint(condition=Q(revision__gte=1), name='homepage_positive_revision'),
        ]

    def __str__(self):
        return 'Personnalisation de l’accueil'


class FeaturedHomepageProduct(models.Model):
    product = models.ForeignKey('core.Product', on_delete=models.CASCADE,
                                related_name='homepage_featured_positions')
    position = models.PositiveSmallIntegerField(unique=True,
        validators=[MinValueValidator(1), MaxValueValidator(4)])

    class Meta:
        ordering = ['position', 'pk']
        verbose_name = 'Produit mis en avant sur l’accueil'
        verbose_name_plural = 'Produits mis en avant sur l’accueil'
        constraints = [
            models.UniqueConstraint(fields=['product'], name='homepage_featured_unique_product'),
            models.CheckConstraint(condition=Q(position__gte=1, position__lte=4), name='homepage_featured_position_range'),
        ]

    def __str__(self):
        return f'{self.position} · {self.product}'


class HeroSlide(models.Model):
    SECTION_CHOICES = (('hero', 'Grande photo d’accueil'), ('feature', 'Invitation au grand air'))

    configuration = models.ForeignKey(HomepageConfiguration, on_delete=models.CASCADE,
                                       related_name='hero_slides')
    section = models.CharField('Bloc de l’accueil', max_length=10,
                               choices=SECTION_CHOICES, default='hero')
    image = models.ImageField('Photo supplémentaire', upload_to=homepage_image_path)
    mobile_image = models.ImageField('Cadrage pour téléphone', upload_to=homepage_image_path, blank=True)
    alt = models.CharField('Description de la photo', max_length=200)
    position = models.PositiveSmallIntegerField('Ordre après la photo principale',
        validators=[MinValueValidator(1), MaxValueValidator(5)])

    class Meta:
        ordering = ['position', 'pk']
        verbose_name = 'Photo du diaporama d’accueil'
        verbose_name_plural = 'Photos du diaporama d’accueil'
        constraints = [
            models.CheckConstraint(condition=Q(configuration_id=1), name='hero_slide_homepage_configuration'),
            models.CheckConstraint(condition=Q(position__gte=1, position__lte=5), name='hero_slide_position_range'),
            models.CheckConstraint(condition=Q(section__in=['hero', 'feature']), name='homepage_slide_valid_section'),
        ]

    def __str__(self):
        return f'Photo supplémentaire {self.position}'
