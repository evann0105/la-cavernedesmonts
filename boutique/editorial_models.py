"""Versioned merchant content, FAQ entries and dated public announcements."""
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from .editorial_schema import (ANNOUNCEMENT_TARGET_CHOICES, KIND_CHOICES, PAGE_SPECS,
                               PRIORITY_CHOICES, TARGET_CHOICES, target_url, validate_page_data)


def editorial_image_path(instance, filename):
    return f'editorial/{uuid4().hex}.webp'


class ContentPage(models.Model):
    key = models.CharField('Page', max_length=12, unique=True,
                           choices=tuple((key, spec['title']) for key, spec in PAGE_SPECS.items()))
    data = models.JSONField(default=dict, blank=True)
    image = models.ImageField('Photo de la page', upload_to=editorial_image_path, blank=True)
    draft_data = models.JSONField(null=True, blank=True, default=None)
    draft_image = models.ImageField('Photo du brouillon', upload_to=editorial_image_path, blank=True)
    published_revision = models.PositiveIntegerField(default=0, editable=False)
    revision = models.PositiveIntegerField(default=1, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False)

    class Meta:
        verbose_name = 'Page de la boutique'
        verbose_name_plural = 'Pages de la boutique'
        constraints = [
            models.CheckConstraint(condition=Q(key__in=('about', 'store')), name='editorial_page_key_valid'),
            models.CheckConstraint(condition=Q(revision__gte=1), name='editorial_page_revision_positive'),
            models.CheckConstraint(condition=Q(published_revision__lte=F('revision')), name='editorial_page_public_revision_valid'),
        ]

    def clean(self):
        super().clean()
        try:
            validate_page_data(self.key, self.data)
            if self.draft_data is not None:
                validate_page_data(self.key, self.draft_data)
        except ValidationError as exc:
            raise ValidationError({'data': exc.messages}) from exc

    @property
    def has_draft(self):
        return self.draft_data is not None

    def __str__(self):
        return PAGE_SPECS.get(self.key, {}).get('title', self.key)


class ContentPageVersion(models.Model):
    """An immutable record of a publication; media files stay at their original path."""
    page = models.ForeignKey(ContentPage, related_name='versions', on_delete=models.CASCADE)
    publication_revision = models.PositiveIntegerField(editable=False)
    data = models.JSONField(default=dict, blank=True, editable=False)
    image = models.ImageField(upload_to=editorial_image_path, blank=True, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False)

    class Meta:
        ordering = ['-publication_revision', '-pk']
        verbose_name = 'Version publiée d’une page'
        verbose_name_plural = 'Versions publiées des pages'
        constraints = [models.UniqueConstraint(fields=('page', 'publication_revision'),
                                               name='editorial_page_version_unique')]

    def clean(self):
        super().clean()
        if self.page_id:
            validate_page_data(self.page.key, self.data)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Une version publiée ne peut pas être modifiée. Restaurez-la dans un brouillon.')
        return super().save(*args, **kwargs)

    def __str__(self):
        return 'Version d’origine' if self.publication_revision == 0 else f'Version {self.publication_revision}'


class FAQEntry(models.Model):
    question = models.CharField('Question', max_length=240)
    answer = models.TextField('Réponse', max_length=4000)
    target = models.CharField('Page liée', max_length=20, choices=TARGET_CHOICES, blank=True)
    link_label = models.CharField('Texte du lien', max_length=100, blank=True)
    position = models.PositiveSmallIntegerField('Ordre d’affichage', default=1,
        validators=[MinValueValidator(1), MaxValueValidator(1000)])
    enabled = models.BooleanField('Afficher cette question', default=True)
    revision = models.PositiveIntegerField(default=1, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False)

    class Meta:
        ordering = ['position', 'pk']
        verbose_name = 'Question fréquente'
        verbose_name_plural = 'Questions fréquentes'
        constraints = [
            models.CheckConstraint(condition=Q(position__gte=1, position__lte=1000), name='editorial_faq_position_valid'),
            models.CheckConstraint(condition=Q(revision__gte=1), name='editorial_faq_revision_positive'),
            models.CheckConstraint(condition=Q(target__in=tuple(key for key, label in TARGET_CHOICES)), name='editorial_faq_target_valid'),
            models.CheckConstraint(condition=(Q(target='', link_label='') | (~Q(target='') & ~Q(link_label=''))), name='editorial_faq_link_paired'),
        ]

    def clean(self):
        super().clean()
        if bool(self.target) != bool(self.link_label.strip()):
            raise ValidationError({'link_label': 'Choisissez une page et un texte pour le lien, ou laissez les deux champs vides.'})

    @property
    def status(self):
        return 'active' if self.enabled else 'inactive'

    @property
    def status_label(self):
        return 'Publiée' if self.enabled else 'Masquée'

    @property
    def link_url(self):
        return target_url(self.target)

    def __str__(self):
        return self.question


class SiteAnnouncement(models.Model):
    name = models.CharField('Nom pour vous retrouver', max_length=120)
    kind = models.CharField('Type d’annonce', max_length=20, choices=KIND_CHOICES, default='information')
    message = models.CharField('Message affiché aux clients', max_length=300)
    starts_at = models.DateTimeField('Début d’affichage')
    ends_at = models.DateTimeField('Fin d’affichage')
    enabled = models.BooleanField('Activer cette annonce aux dates choisies', default=False)
    priority = models.PositiveSmallIntegerField('Priorité', choices=PRIORITY_CHOICES, default=0)
    target = models.CharField('Page liée', max_length=20, choices=ANNOUNCEMENT_TARGET_CHOICES, blank=True)
    link_label = models.CharField('Texte du lien', max_length=100, blank=True)
    revision = models.PositiveIntegerField(default=1, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False)

    class Meta:
        ordering = ['-starts_at', '-pk']
        verbose_name = 'Annonce du site'
        verbose_name_plural = 'Annonces du site'
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F('starts_at')), name='editorial_announcement_dates_valid'),
            models.CheckConstraint(condition=Q(priority__in=(0, 10, 20)), name='editorial_announcement_priority_valid'),
            models.CheckConstraint(condition=Q(kind__in=tuple(key for key, label in KIND_CHOICES)), name='editorial_announcement_kind_valid'),
            models.CheckConstraint(condition=Q(target__in=tuple(key for key, label in ANNOUNCEMENT_TARGET_CHOICES)), name='editorial_announcement_target_valid'),
            models.CheckConstraint(condition=Q(revision__gte=1), name='editorial_announcement_revision_positive'),
            models.CheckConstraint(condition=(Q(target='', link_label='') | (~Q(target='') & ~Q(link_label=''))), name='editorial_announcement_link_paired'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        for name in ('starts_at', 'ends_at'):
            value = getattr(self, name)
            if value is not None and timezone.is_naive(value):
                errors[name] = 'Indiquez une date et une heure avec leur fuseau horaire.'
        if not errors and self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            errors['ends_at'] = 'La fin doit être postérieure au début.'
        if bool(self.target) != bool(self.link_label.strip()):
            errors['link_label'] = 'Choisissez une page et un texte pour le lien, ou laissez les deux champs vides.'
        if errors:
            raise ValidationError(errors)

    def status_at(self, at=None):
        at = timezone.now() if at is None else at
        if not self.enabled:
            return 'draft'
        if at < self.starts_at:
            return 'scheduled'
        if at >= self.ends_at:
            return 'ended'
        return 'active'

    @property
    def status(self):
        return self.status_at()

    @property
    def status_label(self):
        return {'draft': 'Brouillon', 'scheduled': 'Prévue', 'active': 'En cours', 'ended': 'Terminée'}[self.status]

    @property
    def link_url(self):
        return target_url(self.target)

    def __str__(self):
        return self.name
