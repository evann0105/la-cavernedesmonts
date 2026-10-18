from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


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


class OpeningDay(models.Model):
    WEEKDAYS = [(0, 'Lundi'), (1, 'Mardi'), (2, 'Mercredi'), (3, 'Jeudi'),
                (4, 'Vendredi'), (5, 'Samedi'), (6, 'Dimanche')]
    schedule = models.ForeignKey(OpeningHours, related_name='days', on_delete=models.CASCADE)
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    is_closed = models.BooleanField('Fermé', default=True)
    opens_at = models.TimeField('Ouverture', null=True, blank=True)
    closes_at = models.TimeField('Fermeture', null=True, blank=True)
    reopens_at = models.TimeField('Ouverture', null=True, blank=True)
    recloses_at = models.TimeField('Fermeture', null=True, blank=True)

    class Meta:
        ordering = ['weekday']
        verbose_name = 'Jour d’ouverture'
        verbose_name_plural = 'Jours d’ouverture'
        constraints = [
            models.UniqueConstraint(fields=['schedule', 'weekday'], name='one_opening_day_per_weekday'),
            models.CheckConstraint(condition=Q(weekday__gte=0, weekday__lte=6), name='opening_weekday_in_week'),
            models.CheckConstraint(
                condition=(Q(is_closed=True, opens_at__isnull=True, closes_at__isnull=True,
                             reopens_at__isnull=True, recloses_at__isnull=True)
                           | (Q(is_closed=False, opens_at__isnull=False, closes_at__isnull=False,
                                closes_at__gt=F('opens_at'))
                              & (Q(reopens_at__isnull=True, recloses_at__isnull=True)
                                 | Q(reopens_at__isnull=False, recloses_at__isnull=False,
                                     reopens_at__gte=F('closes_at'), recloses_at__gt=F('reopens_at'))))),
                name='valid_daily_opening_periods'),
        ]

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
