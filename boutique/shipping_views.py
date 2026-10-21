"""Delivery settings; all mutations serialize through the policy singleton."""
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods
from core.catalog_access import catalog_required
from .models import ShippingConfiguration, ShippingZone, ShippingCountry
from .shipping import configuration, legacy_threshold, ready
from .shipping_forms import ShippingPolicyForm, ShippingCarrierForm, ShippingZoneForm


class RevisionConflict(ValueError):
    pass


SECTIONS = {
    'policy': {'key': 'policy', 'title': 'Règles de livraison',
        'description': 'Préparation des commandes, livraison offerte et informations pour vos clients.',
        'impact': 'Les nouveaux devis de livraison et les conditions de vente reprennent ces réglages. Les commandes déjà enregistrées conservent leurs tarifs et conditions.'},
    'carrier': {'key': 'carrier', 'title': 'Transporteur',
        'description': 'Renseignez le transporteur choisi et le nom du service proposé.',
        'impact': 'Ces informations décrivent votre service de livraison. Le lien vers le transporteur reste manuel : aucune étiquette ni connexion à un compte transporteur n’est créée.'},
    'zone': {'key': 'zone', 'title': 'Zone de livraison',
        'description': 'Regroupez les pays qui partagent les mêmes frais et délais.',
        'impact': 'Chaque pays appartient à une seule zone. Les zones désactivées ne sont pas proposées aux clients. Modifier une zone ne change pas les commandes déjà enregistrées.'},
}


def _context(current):
    return {'shipping_configuration': current, 'zones': current['zones'],
            'ready': ready(current), 'shipping_ready': ready(current), 'blockers': current['blockers']}


def _locked_configuration(expected_revision):
    instance = ShippingConfiguration.objects.select_for_update().filter(pk=1).first()
    revision = instance.revision if instance else 0
    if expected_revision != revision:
        raise RevisionConflict
    if instance is None:
        instance = ShippingConfiguration(free_shipping_threshold=legacy_threshold())
    return instance


def _save_configuration(instance, actor, expected_revision, fields=()):
    instance.revision = expected_revision + 1
    instance.updated_by = actor
    if instance._state.adding:
        instance.save(force_insert=True)
    else:
        instance.save(update_fields=list(fields) + ['revision', 'updated_by', 'updated_at'])


def _log(request, obj, label, new=False):
    LogEntry.objects.log_actions(user_id=request.user.pk,
        queryset=type(obj).objects.filter(pk=obj.pk), action_flag=ADDITION if new else CHANGE,
        change_message=label)


def _stale(form):
    form.add_error(None, 'Les réglages de livraison ont été modifiés dans un autre onglet. Rechargez la page avant de les enregistrer à nouveau.')


@catalog_required
@require_GET
def overview(request):
    current = configuration()
    return render(request, 'boutique/shipping/overview.html', {
        'title': 'Livraison', **_context(current),
    })


def _policy_edit(request, key, form_class):
    current = configuration()
    form = form_class(request.POST if request.method == 'POST' else None, initial=current)
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                expected = form.cleaned_data['revision']
                instance = _locked_configuration(expected)
                new = instance._state.adding
                fields = [field for field in form.cleaned_data if field != 'revision']
                for field in fields:
                    setattr(instance, field, form.cleaned_data[field])
                _save_configuration(instance, request.user, expected, fields)
                _log(request, instance, 'Livraison : ' + SECTIONS[key]['title'] + '.', new)
        except (RevisionConflict, IntegrityError):
            _stale(form)
            status = 409
        else:
            messages.success(request, 'Les réglages de livraison ont été enregistrés.')
            return redirect('boutique:shipping_overview')
    return render(request, f'boutique/shipping/{key}.html', {
        'title': SECTIONS[key]['title'], 'section': SECTIONS[key], 'form': form,
        **_context(current),
    }, status=status)


@catalog_required
@require_http_methods(['GET', 'POST'])
def policy(request):
    return _policy_edit(request, 'policy', ShippingPolicyForm)


@catalog_required
@require_http_methods(['GET', 'POST'])
def carrier(request):
    return _policy_edit(request, 'carrier', ShippingCarrierForm)


def _zone_edit(request, pk=None):
    current = configuration()
    zone = get_object_or_404(ShippingZone, pk=pk, configuration_id=1) if pk is not None else None
    initial = {'title': '', 'price': None, 'free_shipping_eligible': True,
               'delivery_days_min': None, 'delivery_days_max': None, 'active': False,
               'revision': 0, 'configuration_revision': current['revision'], 'countries': []}
    if zone:
        initial.update({field: getattr(zone, field) for field in
                       ('title', 'price', 'free_shipping_eligible', 'delivery_days_min',
                        'delivery_days_max', 'active', 'revision')})
        initial['countries'] = list(zone.countries.values_list('code', flat=True))
    form = ShippingZoneForm(request.POST if request.method == 'POST' else None, initial=initial, zone=zone)
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                expected = form.cleaned_data['configuration_revision']
                instance = _locked_configuration(expected)
                locked_zone = ShippingZone.objects.select_for_update().filter(pk=pk, configuration_id=1).first() if pk is not None else None
                if (pk is not None and locked_zone is None) or form.cleaned_data['revision'] != (locked_zone.revision if locked_zone else 0):
                    raise RevisionConflict
                countries = form.cleaned_data['countries']
                conflicts = ShippingCountry.objects.filter(code__in=countries)
                if locked_zone:
                    conflicts = conflicts.exclude(zone=locked_zone)
                if conflicts.exists():
                    raise RevisionConflict
                new = locked_zone is None
                _save_configuration(instance, request.user, expected)
                saved = locked_zone or ShippingZone(configuration=instance)
                for field in ('title', 'price', 'free_shipping_eligible', 'delivery_days_min',
                              'delivery_days_max', 'active'):
                    setattr(saved, field, form.cleaned_data[field])
                saved.revision = (locked_zone.revision if locked_zone else 0) + 1
                saved.updated_by = request.user
                saved.save()
                saved.countries.exclude(code__in=countries).delete()
                existing = set(saved.countries.values_list('code', flat=True))
                ShippingCountry.objects.bulk_create([
                    ShippingCountry(zone=saved, code=code) for code in countries if code not in existing])
                _log(request, saved, 'Zone de livraison créée.' if new else 'Zone de livraison modifiée.', new)
        except (RevisionConflict, IntegrityError):
            _stale(form)
            status = 409
        else:
            messages.success(request, 'La zone de livraison a été enregistrée.')
            return redirect('boutique:shipping_overview')
    return render(request, 'boutique/shipping/zone.html', {
        'title': 'Ajouter une zone' if zone is None else 'Modifier la zone',
        'section': SECTIONS['zone'], 'form': form, 'zone': zone, 'is_new': zone is None,
        **_context(current),
    }, status=status)


@catalog_required
@require_http_methods(['GET', 'POST'])
def zone_add(request):
    return _zone_edit(request)


@catalog_required
@require_http_methods(['GET', 'POST'])
def zone_edit(request, pk):
    return _zone_edit(request, pk)


@require_GET
def information(request):
    from .shipping import legal_delivery
    current = configuration()
    return render(request, 'boutique/shipping/information.html', {
        'title': 'Livraison et délais', 'shipping_configuration': current,
        'delivery_text': legal_delivery(current),
    })
