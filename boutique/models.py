from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
from .configuration import DEFAULTS as CONFIGURATION_DEFAULTS


class OpeningHours(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    notice = models.TextField('Information complémentaire', max_length=600, blank=True)
    revision = models.PositiveIntegerField(default=1, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False)

    class Meta:
        verbose_name = 'Horaires du magasin'
        verbose_name_plural = 'Horaires du magasin'
        constraints = [models.CheckConstraint(condition=Q(id=1), name='one_shop_opening_schedule')]

    def __str__(self):
        return 'Horaires du magasin'


class OpeningPeriod(models.Model):
    schedule = models.ForeignKey(OpeningHours, related_name='periods', on_delete=models.CASCADE)
    name = models.CharField('Nom de la période', max_length=120)
    start_date = models.DateField('Date de début')
    end_date = models.DateField('Date de fin')
    notice = models.TextField('Information complémentaire', max_length=600, blank=True)
    is_cancelled = models.BooleanField(default=False, editable=False)
    revision = models.PositiveIntegerField(default=1, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False)

    class Meta:
        ordering = ['start_date', 'pk']
        verbose_name = 'Période d’horaires exceptionnels'
        verbose_name_plural = 'Périodes d’horaires exceptionnels'
        constraints = [models.CheckConstraint(condition=Q(end_date__gte=F('start_date')),
                                              name='opening_period_valid_dates')]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not self.start_date or not self.end_date:
            return
        if self.end_date < self.start_date:
            raise ValidationError({'end_date': 'La fin doit être le même jour ou après le début.'})
        if not self.is_cancelled and self.schedule_id:
            overlapping = type(self).objects.filter(
                schedule_id=self.schedule_id, is_cancelled=False,
                start_date__lte=self.end_date, end_date__gte=self.start_date,
            ).exclude(pk=self.pk)
            if overlapping.exists():
                raise ValidationError('Ces dates chevauchent une autre période. Choisissez des dates distinctes ou annulez l’autre période.')

    @property
    def status(self):
        if self.is_cancelled:
            return 'cancelled'
        today = timezone.localdate(timezone=ZoneInfo('Europe/Paris'))
        if self.start_date and today < self.start_date:
            return 'upcoming'
        if self.end_date and today > self.end_date:
            return 'past'
        return 'active'

    @property
    def status_class(self):
        return self.status

    @property
    def status_label(self):
        return {'cancelled': 'Annulée', 'upcoming': 'À venir', 'past': 'Terminée', 'active': 'En cours'}[self.status]


def daily_time_constraint(name):
    return models.CheckConstraint(
        condition=(Q(is_closed=True, opens_at__isnull=True, closes_at__isnull=True,
                     reopens_at__isnull=True, recloses_at__isnull=True)
                   | (Q(is_closed=False, opens_at__isnull=False, closes_at__isnull=False,
                        closes_at__gt=F('opens_at'))
                      & (Q(reopens_at__isnull=True, recloses_at__isnull=True)
                         | Q(reopens_at__isnull=False, recloses_at__isnull=False,
                             reopens_at__gte=F('closes_at'), recloses_at__gt=F('reopens_at'))))),
        name=name,
    )


class WeeklyDay(models.Model):
    WEEKDAYS = [(0, 'Lundi'), (1, 'Mardi'), (2, 'Mercredi'), (3, 'Jeudi'),
                (4, 'Vendredi'), (5, 'Samedi'), (6, 'Dimanche')]
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    is_closed = models.BooleanField('Fermé', default=True)
    opens_at = models.TimeField('Ouverture', null=True, blank=True)
    closes_at = models.TimeField('Fermeture', null=True, blank=True)
    reopens_at = models.TimeField('Ouverture', null=True, blank=True)
    recloses_at = models.TimeField('Fermeture', null=True, blank=True)

    class Meta:
        abstract = True
        ordering = ['weekday']

    def __str__(self):
        return self.get_weekday_display()

    def clean(self):
        super().clean()
        if self.is_closed:
            self.opens_at = self.closes_at = self.reopens_at = self.recloses_at = None
            return
        errors = {}
        for field in ('opens_at', 'closes_at'):
            if getattr(self, field) is None:
                errors[field] = 'Renseignez le premier créneau ou cochez « Fermé ».'
        if self.opens_at and self.closes_at and self.closes_at <= self.opens_at:
            errors['closes_at'] = 'La fermeture doit être après l’ouverture.'
        if bool(self.reopens_at) != bool(self.recloses_at):
            errors['recloses_at' if self.reopens_at else 'reopens_at'] = 'Complétez les deux heures du second créneau.'
        if self.reopens_at and self.recloses_at and self.recloses_at <= self.reopens_at:
            errors['recloses_at'] = 'La fermeture doit être après l’ouverture.'
        if self.reopens_at and self.closes_at and self.reopens_at < self.closes_at:
            errors['reopens_at'] = 'Le second créneau doit commencer après le premier.'
        if errors:
            raise ValidationError(errors)


class OpeningDay(WeeklyDay):
    schedule = models.ForeignKey(OpeningHours, related_name='days', on_delete=models.CASCADE)

    class Meta:
        ordering = ['weekday']
        verbose_name = 'Jour d’ouverture'
        verbose_name_plural = 'Jours d’ouverture'
        constraints = [
            models.UniqueConstraint(fields=['schedule', 'weekday'], name='one_opening_day_per_weekday'),
            models.CheckConstraint(condition=Q(weekday__gte=0, weekday__lte=6), name='opening_weekday_in_week'),
            daily_time_constraint('valid_daily_opening_periods'),
        ]


class OpeningPeriodDay(WeeklyDay):
    period = models.ForeignKey(OpeningPeriod, related_name='days', on_delete=models.CASCADE)

    class Meta:
        ordering = ['weekday']
        verbose_name = 'Jour d’ouverture exceptionnel'
        verbose_name_plural = 'Jours d’ouverture exceptionnels'
        constraints = [
            models.UniqueConstraint(fields=['period', 'weekday'], name='one_period_day_per_weekday'),
            models.CheckConstraint(condition=Q(weekday__gte=0, weekday__lte=6), name='period_weekday_in_week'),
            daily_time_constraint('valid_period_daily_times'),
        ]


class ShopConfiguration(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    phone = models.CharField('Téléphone de la boutique', max_length=64, default=CONFIGURATION_DEFAULTS['phone'])
    email = models.EmailField('Adresse e-mail publique', max_length=254, default=CONFIGURATION_DEFAULTS['email'])
    store_address_line1 = models.CharField('Numéro et rue', max_length=255, default=CONFIGURATION_DEFAULTS['store_address_line1'])
    store_address_line2 = models.CharField('Complément d’adresse', max_length=255, blank=True, default=CONFIGURATION_DEFAULTS['store_address_line2'])
    store_postal_code = models.CharField('Code postal', max_length=20, default=CONFIGURATION_DEFAULTS['store_postal_code'])
    store_city = models.CharField('Ville', max_length=120, default=CONFIGURATION_DEFAULTS['store_city'])
    store_country = models.CharField('Pays', max_length=80, default=CONFIGURATION_DEFAULTS['store_country'])
    business_name = models.CharField('Nom juridique de l’entreprise', max_length=255, default=CONFIGURATION_DEFAULTS['business_name'])
    legal_form = models.CharField('Forme juridique', max_length=80, default=CONFIGURATION_DEFAULTS['legal_form'])
    siren = models.CharField('SIREN', max_length=20, default=CONFIGURATION_DEFAULTS['siren'])
    siret = models.CharField('SIRET', max_length=25, default=CONFIGURATION_DEFAULTS['siret'])
    registration = models.CharField('Immatriculation', max_length=255, default=CONFIGURATION_DEFAULTS['registration'])
    publisher = models.CharField('Responsable de publication', max_length=255, blank=True, default='')
    business_address = models.TextField('Adresse professionnelle distincte', max_length=2000, blank=True, default=CONFIGURATION_DEFAULTS['business_address'])
    business_same_as_store = models.BooleanField('L’adresse professionnelle est celle du magasin', default=False)
    return_address = models.TextField('Adresse de retour distincte', max_length=2000, blank=True, default=CONFIGURATION_DEFAULTS['return_address'])
    returns_same_as_store = models.BooleanField('Utiliser la même adresse que le magasin pour les retours', default=False)
    revision = models.PositiveIntegerField(default=1, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False)

    class Meta:
        verbose_name = 'Réglages de la boutique'
        verbose_name_plural = 'Réglages de la boutique'
        constraints = [
            models.CheckConstraint(condition=Q(id=1), name='one_shop_configuration'),
            models.CheckConstraint(condition=Q(revision__gte=1), name='shop_configuration_positive_revision'),
        ]

    def __str__(self):
        return 'Réglages de la boutique'


def shipping_days_constraint(minimum, maximum, name):
    return models.CheckConstraint(condition=(
        Q(**{minimum + '__isnull': True, maximum + '__isnull': True})
        | Q(**{minimum + '__isnull': False, maximum + '__isnull': False,
               minimum + '__gte': 0, maximum + '__lte': 60,
               maximum + '__gte': F(minimum)})
    ), name=name)


class ShippingConfiguration(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField('Autoriser la livraison en ligne', default=True)
    free_shipping_enabled = models.BooleanField('Livraison offerte à partir d’un seuil', default=True)
    free_shipping_threshold = models.DecimalField('Seuil de livraison offerte (€)', max_digits=10,
                                                   decimal_places=2, default=Decimal('99.00'))
    preparation_days_min = models.PositiveSmallIntegerField('Préparation minimum (jours ouvrés)', null=True, blank=True)
    preparation_days_max = models.PositiveSmallIntegerField('Préparation maximum (jours ouvrés)', null=True, blank=True)
    carrier_name = models.CharField('Nom du transporteur', max_length=150, blank=True)
    service_name = models.CharField('Nom du service', max_length=150, default='Livraison standard')
    carrier_website = models.URLField('Site du transporteur', max_length=500, blank=True)
    public_notice = models.TextField('Information pour les clients', max_length=1500, blank=True)
    revision = models.PositiveIntegerField(default=1, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False)

    class Meta:
        verbose_name = 'Réglages de livraison'
        verbose_name_plural = 'Réglages de livraison'
        constraints = [
            models.CheckConstraint(condition=Q(id=1), name='one_shipping_configuration'),
            models.CheckConstraint(condition=Q(revision__gte=1), name='shipping_config_positive_revision'),
            models.CheckConstraint(condition=Q(free_shipping_threshold__gte=0), name='shipping_threshold_nonnegative'),
            shipping_days_constraint('preparation_days_min', 'preparation_days_max', 'shipping_preparation_days_valid'),
        ]

    def __str__(self):
        return 'Réglages de livraison'


class ShippingZone(models.Model):
    configuration = models.ForeignKey(ShippingConfiguration, default=1,
                                       related_name='zones', on_delete=models.PROTECT)
    title = models.CharField('Nom de la zone', max_length=150)
    price = models.DecimalField('Frais de livraison standard (€)', max_digits=10,
                                 decimal_places=2, null=True, blank=True)
    free_shipping_eligible = models.BooleanField('Livraison offerte possible', default=True)
    delivery_days_min = models.PositiveSmallIntegerField('Acheminement minimum (jours ouvrés)', null=True, blank=True)
    delivery_days_max = models.PositiveSmallIntegerField('Acheminement maximum (jours ouvrés)', null=True, blank=True)
    active = models.BooleanField('Zone active', default=False)
    revision = models.PositiveIntegerField(default=1, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False)

    class Meta:
        ordering = ['title', 'pk']
        verbose_name = 'Zone de livraison'
        verbose_name_plural = 'Zones de livraison'
        constraints = [
            models.CheckConstraint(condition=Q(price__isnull=True) | Q(price__gte=0), name='shipping_zone_price_nonnegative'),
            models.CheckConstraint(condition=Q(revision__gte=1), name='shipping_zone_positive_revision'),
            shipping_days_constraint('delivery_days_min', 'delivery_days_max', 'shipping_zone_days_valid'),
            models.CheckConstraint(condition=Q(active=False) | Q(price__isnull=False, delivery_days_min__isnull=False,
                                                                 delivery_days_max__isnull=False), name='active_shipping_zone_complete'),
        ]

    def __str__(self):
        return self.title


class ShippingCountry(models.Model):
    zone = models.ForeignKey(ShippingZone, related_name='countries', on_delete=models.CASCADE)
    code = models.CharField('Pays', max_length=2, unique=True)

    class Meta:
        ordering = ['code']
        verbose_name = 'Pays de livraison'
        verbose_name_plural = 'Pays de livraison'

    def __str__(self):
        return self.code


# Keep editorial models in their own module without introducing another app.
from .editorial_models import ContentPage, ContentPageVersion, FAQEntry, SiteAnnouncement  # noqa: E402,F401
