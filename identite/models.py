from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.utils.translation import gettext_lazy as _
from config.encrypted_fields import EncryptedCharField, IndexedEncryptedEmailField, email_digest


class PrivateUserQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if 'email' in kwargs:
            if not isinstance(kwargs['email'], str):
                raise ValueError('Adresse e-mail littérale requise.')
            kwargs['email_hash'] = email_digest(kwargs['email'])
        return super().update(**kwargs)

    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        for obj in objs:
            obj.email_hash = email_digest(obj.email)
        return super().bulk_create(objs, **kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        if 'email' in fields:
            raise ValueError('Utilisez save() pour modifier les adresses e-mail.')
        return super().bulk_update(objs, fields, **kwargs)


class PrivateUserManager(UserManager.from_queryset(PrivateUserQuerySet)):
    use_in_migrations = True


class User(AbstractUser):
    first_name = EncryptedCharField(_('first name'), max_length=150, blank=True)
    last_name = EncryptedCharField(_('last name'), max_length=150, blank=True)
    email = IndexedEncryptedEmailField(_('email address'), max_length=254, blank=True)
    email_hash = models.CharField(max_length=64, db_index=True, editable=False, default='')
    objects = PrivateUserManager()

    class Meta(AbstractUser.Meta):
        constraints = [models.UniqueConstraint(fields=['email_hash'], condition=~models.Q(email_hash=''), name='unique_nonempty_user_email')]

    def save(self, *args, **kwargs):
        self.email_hash = email_digest(self.email)
        if kwargs.get('update_fields') is not None and 'email' in kwargs['update_fields']:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'email_hash'}
        super().save(*args, **kwargs)
