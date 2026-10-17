"""Authenticated field encryption. Keys must be backed up separately from the DB."""
import base64
import hashlib
import hmac
import json
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.lookups import Exact, IExact
from django.db.models.expressions import Col

PREFIX = 'enc:v1:'

def key():
    path = getattr(settings, 'DATA_ENCRYPTION_KEY_FILE', '')
    if not path:
        raise ImproperlyConfigured('DATA_ENCRYPTION_KEY_FILE est obligatoire.')
    value = Path(path).read_bytes().strip()
    Fernet(value)  # Validate rather than silently generating a replacement key.
    return value


def encrypt(value):
    return PREFIX + Fernet(key()).encrypt(value.encode('utf-8')).decode('ascii')


def decrypt(value):
    if not value.startswith(PREFIX):
        raise ValueError('Donnée personnelle non chiffrée : migration nécessaire.')
    try:
        return Fernet(key()).decrypt(value[len(PREFIX):].encode('ascii')).decode('utf-8')
    except InvalidToken as exc:
        raise ValueError('Clé incorrecte ou donnée chiffrée altérée.') from exc


def email_digest(value):
    if not value:
        return ''
    return hmac.new(base64.urlsafe_b64decode(key()), b'email-index:v1\0' + value.strip().lower().encode(), hashlib.sha256).hexdigest()


class EncryptedTextField(models.TextField):
    def from_db_value(self, value, expression, connection):
        return None if value is None else decrypt(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return None if value is None else encrypt(value)

    def get_lookup(self, name):
        if name != 'isnull':
            raise ValueError('Recherche SQL interdite sur un champ chiffré sans index dédié.')
        return super().get_lookup(name)


class EncryptedCharField(EncryptedTextField):
    def formfield(self, **kwargs):
        from django import forms
        kwargs.setdefault('widget', forms.TextInput)
        return super().formfield(**kwargs)


class EncryptedEmailField(EncryptedCharField):
    default_validators = models.EmailField.default_validators
    def formfield(self, **kwargs):
        from django import forms
        kwargs['form_class'] = forms.EmailField
        return super().formfield(**kwargs)


class IndexedEmailExact(Exact):
    prepare_rhs = False
    def as_sql(self, compiler, connection):
        if not isinstance(self.rhs, str):
            raise ValueError('Une adresse littérale est requise.')
        field = self.lhs.target.model._meta.get_field('email_hash')
        lhs, params = compiler.compile(Col(self.lhs.alias, field))
        return f'{lhs} = %s', [*params, email_digest(self.rhs)]


class IndexedEmailIExact(IndexedEmailExact, IExact):
    lookup_name = 'iexact'


class IndexedEncryptedEmailField(EncryptedEmailField):
    def get_lookup(self, name):
        if name == 'exact':
            return IndexedEmailExact
        if name == 'iexact':
            return IndexedEmailIExact
        return super().get_lookup(name)


class EncryptedJSONField(EncryptedTextField):
    def from_db_value(self, value, expression, connection):
        return None if value is None else json.loads(decrypt(value))

    def to_python(self, value):
        if isinstance(value, str):
            return json.loads(value)
        return value

    def get_prep_value(self, value):
        return None if value is None else encrypt(json.dumps(value, ensure_ascii=False))

    def value_to_string(self, obj):
        return self.value_from_object(obj)

    def formfield(self, **kwargs):
        from django import forms
        kwargs['form_class'] = forms.JSONField
        return super().formfield(**kwargs)
