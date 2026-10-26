from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .access import authorize_configuration
from .forms import AlertSettingsForm
from .models import AlertSettings, AlertEvent


def save_settings(actor, data):
    authorize_configuration(actor)
    form = AlertSettingsForm(data)
    if not form.is_valid():
        raise ValidationError(form.errors.as_data())
    values = form.cleaned_data
    with transaction.atomic():
        current = AlertSettings.objects.select_for_update().filter(pk=1).first()
        revision = current.revision if current else 0
        if values['revision'] != revision:
            raise ValidationError('Les réglages ont été modifiés dans un autre onglet. Rechargez la page avant d’enregistrer.')
        instance = current or AlertSettings(pk=1)
        changes = {}
        for name in ('recipient', 'order_notifications_enabled', 'stock_notifications_enabled', 'stock_threshold'):
            before, after = getattr(instance, name), values[name]
            if before != after:
                changes[name] = {'before': before, 'after': after}
                setattr(instance, name, after)
        if not changes and current:
            return instance
        instance.revision = revision + 1
        if current and any(name in changes for name in ('recipient', 'stock_notifications_enabled', 'stock_threshold')):
            instance.stock_generation += 1
        instance.updated_by = actor
        instance.full_clean()
        try:
            with transaction.atomic():
                instance.save(force_insert=current is None)
        except IntegrityError as exc:
            raise ValidationError('Les réglages viennent d’être enregistrés ailleurs. Rechargez la page.') from exc
        AlertEvent.objects.create(actor=actor, kind='settings_changed', details=changes)
        return instance
