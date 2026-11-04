import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def folder_key(name):
    return unicodedata.normalize('NFKC', name.strip()).casefold()


class Folder(models.Model):
    name = models.CharField('Nom du dossier', max_length=80)
    name_key = models.CharField(max_length=240, unique=True, editable=False)
    system_key = models.CharField(max_length=16, unique=True, null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name_key', 'pk']

    def save(self, *args, **kwargs):
        self.name = unicodedata.normalize('NFKC', self.name.strip())
        self.name_key = folder_key(self.name)
        if not self.name or len(self.name) > 80:
            raise ValidationError('Choisissez un nom de dossier de 1 à 80 caractères.')
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'name', 'name_key'}
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


IMMUTABLE_PHOTO_FIELDS = frozenset({'image', 'thumbnail', 'digest', 'source_digest', 'original_name',
                                  'width', 'height', 'size', 'created_at', 'source_label'})


class PhotoQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if IMMUTABLE_PHOTO_FIELDS.intersection(kwargs):
            raise ValidationError('Le fichier d’une photo conservée ne peut pas être remplacé.')
        return super().update(**kwargs)

    def delete(self):
        raise ValidationError('Archivez les photos pour les retirer de la bibliothèque.')


class Photo(models.Model):
    image = models.ImageField(upload_to='phototheque/images/', max_length=255, editable=False)
    thumbnail = models.ImageField(upload_to='phototheque/thumbnails/', max_length=255, editable=False)
    digest = models.CharField(max_length=64, unique=True, editable=False)
    source_digest = models.CharField(max_length=64, db_index=True, editable=False)
    title = models.CharField('Nom de la photo', max_length=160)
    original_name = models.CharField(max_length=255, editable=False)
    width = models.PositiveIntegerField(editable=False)
    height = models.PositiveIntegerField(editable=False)
    size = models.PositiveIntegerField(editable=False)
    folder = models.ForeignKey(Folder, on_delete=models.PROTECT, related_name='photos', null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, editable=False)
    source_label = models.CharField(max_length=160, blank=True, editable=False)
    objects = PhotoQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.CheckConstraint(condition=models.Q(width__gt=0, height__gt=0, size__gt=0), name='photo_positive_dimensions_size')]
        indexes = [models.Index(fields=['archived_at', 'folder', '-created_at'], name='photo_library_listing')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            if any(getattr(previous, name) != getattr(self, name) for name in IMMUTABLE_PHOTO_FIELDS):
                raise ValidationError('Le fichier d’une photo conservée ne peut pas être remplacé.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Archivez les photos pour les retirer de la bibliothèque.')

    def __str__(self):
        return self.title
