"""Authorized, optimistic saves for the home editor, including upload cleanup."""
from copy import deepcopy

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.core.exceptions import PermissionDenied
from django.core.files.base import File
from django.db import IntegrityError, transaction
from django.db.models.fields.files import FieldFile

from connexion.email_verification import email_access_allowed
from core.catalog_access import can_manage_catalog
from .configuration import configuration
from .forms import (FeaturedProductsForm, HomepageImageInitial, SectionForm,
                    SectionsVisibilityForm)
from .models import FeaturedHomepageProduct, HomepageConfiguration
from .schema import OPTIONAL_SECTIONS, SECTIONS


class HomepageError(ValueError):
    pass


class HomepageConflict(HomepageError):
    pass


def _authorized(actor):
    if actor is None or not can_manage_catalog(actor) or not email_access_allowed(actor):
        raise PermissionDenied


def _revision(cleaned_data):
    if not isinstance(cleaned_data, dict) or type(cleaned_data.get('revision')) is not int or cleaned_data['revision'] < 0:
        raise HomepageError('Rechargez la page pour vérifier la version actuelle de l’accueil.')
    return cleaned_data['revision']


def _validate(form):
    if not form.is_valid():
        errors = []
        for name, messages in form.errors.items():
            label = form.fields[name].label if name in form.fields else ''
            errors.extend((str(label) + ' : ' if label else '') + str(message) for message in messages)
        raise HomepageError('Vérifiez les informations. ' + ' '.join(errors))
    return form.cleaned_data


def _locked(expected_revision):
    instance = HomepageConfiguration.objects.select_for_update().filter(pk=1).first()
    revision = instance.revision if instance is not None else 0
    if revision != expected_revision:
        raise HomepageConflict('L’accueil a été modifié dans un autre onglet. Rechargez la page avant de continuer.')
    return instance or HomepageConfiguration(), instance is None


def _save(instance, is_new, actor, label):
    instance.revision = (0 if is_new else instance.revision) + 1
    instance.updated_by = actor
    # The fixed primary key and revision are service-owned. Unique checks here
    # would race an absent singleton; force_insert below handles that case.
    instance.full_clean(validate_unique=False, validate_constraints=False)
    try:
        with transaction.atomic():
            instance.save(force_insert=is_new)
    except IntegrityError as error:
        if is_new:
            raise HomepageConflict('L’accueil vient d’être modifié dans un autre onglet. Rechargez la page avant de continuer.') from error
        raise
    LogEntry.objects.log_actions(user_id=actor.pk,
        queryset=HomepageConfiguration.objects.filter(pk=1),
        action_flag=ADDITION if is_new else CHANGE,
        change_message='Personnalisation de l’accueil : ' + label + '.')


def _data(instance):
    data = deepcopy(instance.data) if isinstance(instance.data, dict) else {}
    for key in ('values', 'visible'):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    return data


def _cleanup_new_files(instance, names, original_names):
    if instance is None:
        return
    for name in names:
        field_file = getattr(instance, name)
        if (field_file.name and field_file.name != original_names.get(name)
                and field_file.name.startswith('homepage/') and field_file._committed):
            # Previous photos are intentionally retained: another transaction
            # or a restored backup may still refer to them.
            field_file.storage.delete(field_file.name)


def save_section(key, cleaned_data, actor):
    _authorized(actor)
    expected_revision = _revision(cleaned_data)
    if not isinstance(key, str) or key not in SECTIONS:
        raise HomepageError('Cette section de l’accueil n’existe pas.')
    instance, uploaded_fields, original_names = None, [], {}
    try:
        with transaction.atomic():
            instance, is_new = _locked(expected_revision)
            current = configuration(instance=instance)
            values = {'revision': expected_revision}
            files = {}
            for name in SECTIONS[key]['fields']:
                if name in cleaned_data:
                    if not isinstance(cleaned_data[name], str):
                        raise HomepageError('Les textes et destinations doivent être renseignés sous forme de texte.')
                    values[name] = cleaned_data[name]
            for name in SECTIONS[key]['images']:
                original_names[name] = getattr(instance, name).name
                value = cleaned_data.get(name)
                if value is False:
                    values[name + '-clear'] = True
                elif value is None or isinstance(value, HomepageImageInitial):
                    continue
                elif isinstance(value, FieldFile):
                    if value.name != original_names[name]:
                        raise HomepageError('Ajoutez la photo depuis le sélecteur de fichiers.')
                elif isinstance(value, File):
                    files[name] = value
                else:
                    raise HomepageError('Choisissez une photo JPEG, PNG ou WebP valide.')
            validated = _validate(SectionForm(key, values, files, configuration=current))
            data = _data(instance)
            data['values'][key] = {name: validated[name] for name in SECTIONS[key]['fields']}
            instance.data = data
            for name in SECTIONS[key]['images']:
                value = validated[name]
                if value is False:
                    setattr(instance, name, '')
                elif isinstance(value, File) and not isinstance(value, FieldFile):
                    uploaded_fields.append(name)
                    setattr(instance, name, value)
            _save(instance, is_new, actor, SECTIONS[key]['title'])
            return instance
    except Exception:
        _cleanup_new_files(instance, uploaded_fields, original_names)
        raise


def save_visibility(cleaned_data, actor):
    _authorized(actor)
    expected_revision = _revision(cleaned_data)
    if any(type(cleaned_data.get(key)) is not bool for key in OPTIONAL_SECTIONS):
        raise HomepageError('Choisissez les sections à afficher à l’aide des cases de cette page.')
    with transaction.atomic():
        instance, is_new = _locked(expected_revision)
        current = configuration(instance=instance)
        validated = _validate(SectionsVisibilityForm(cleaned_data, configuration=current))
        data = _data(instance)
        data['visible'] = {key: validated[key] for key in OPTIONAL_SECTIONS}
        data['visible']['hero'] = True
        instance.data = data
        _save(instance, is_new, actor, 'Sections affichées')
        return instance


def save_featured(cleaned_data, actor):
    _authorized(actor)
    expected_revision = _revision(cleaned_data)
    with transaction.atomic():
        instance, is_new = _locked(expected_revision)
        current = configuration(instance=instance)
        if any(type(cleaned_data.get(f'position_{position}')) is bool for position in range(1, 5)):
            raise HomepageError('Choisissez les produits dans les listes proposées.')
        validated = _validate(FeaturedProductsForm(cleaned_data, configuration=current))
        _save(instance, is_new, actor, 'Produits mis en avant')
        FeaturedHomepageProduct.objects.all().delete()
        # Compact empty positions: the first chosen product is always first on
        # the storefront, and the editor faithfully shows that same sequence.
        products = [validated[f'position_{position}'] for position in range(1, 5)
                    if validated[f'position_{position}'] is not None]
        FeaturedHomepageProduct.objects.bulk_create([
            FeaturedHomepageProduct(product=product, position=position)
            for position, product in enumerate(products, 1)
        ])
        return instance
