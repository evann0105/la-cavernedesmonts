from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from zoneinfo import ZoneInfo


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
