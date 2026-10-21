"""Merchant-facing settings, with one transaction and revision per saved section."""
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from core.catalog_access import catalog_required
from .configuration import configuration, stored_initial
from .configuration_forms import ContactsForm, StoreAddressForm, ReturnsAddressForm, CompanyForm
from .models import ShopConfiguration


SECTIONS = (
    {'key': 'contacts', 'title': 'Téléphone et e-mail',
     'description': 'Les coordonnées utilisées par les clients pour vous joindre.',
     'urlname': 'boutique:settings_contacts',
     'impact': 'Ces coordonnées sont reprises sur les pages du site, les contacts des e-mails et les nouveaux documents. Le service d’envoi des e-mails reste inchangé.'},
    {'key': 'store', 'title': 'Adresse du magasin',
     'description': 'L’adresse où les visiteurs peuvent retrouver la boutique.',
     'urlname': 'boutique:settings_store',
     'impact': 'Cette adresse met à jour les informations pratiques, l’itinéraire et les informations de la boutique pour Google. Les adresses liées au magasin la suivent automatiquement.'},
    {'key': 'returns', 'title': 'Adresse de retour',
     'description': 'Choisissez où les clients doivent renvoyer leurs articles.',
     'urlname': 'boutique:settings_returns',
     'impact': 'Cette adresse est utilisée dans les instructions de retour et les nouveaux accusés de rétractation. Vous pouvez reprendre l’adresse du magasin ou indiquer une adresse différente.'},
    {'key': 'company', 'title': 'Informations de l’entreprise',
     'description': 'L’identité juridique de la boutique et son adresse professionnelle.',
     'urlname': 'boutique:settings_company',
     'impact': 'Ces informations sont reprises dans les pages légales et les nouveaux documents de vente. Les commandes et factures déjà enregistrées conservent leurs informations d’origine.'},
)
FORMS = {'contacts': ContactsForm, 'store': StoreAddressForm,
         'returns': ReturnsAddressForm, 'company': CompanyForm}


@catalog_required
@require_GET
def settings(request):
    current = configuration()
    return render(request, 'boutique/settings/index.html', {
        'title': 'Réglages de la boutique', 'sections': SECTIONS,
        'configuration': current, 'shop_configuration': current,
    })


def _stale(form):
    form.add_error(None, 'Ces informations ont été modifiées dans un autre onglet. Rechargez la page avant de les enregistrer à nouveau.')


def _edit(request, key):
    section = next(section for section in SECTIONS if section['key'] == key)
    current = configuration()
    initial = {**stored_initial(current), 'revision': current['revision']}
    form = FORMS[key](request.POST if request.method == 'POST' else None, initial=initial)
    status = 200
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            instance = ShopConfiguration.objects.select_for_update().filter(pk=1).first()
            revision = instance.revision if instance else 0
            if form.cleaned_data['revision'] != revision:
                _stale(form)
                status = 409
            else:
                is_new = instance is None
                if is_new:
                    # Preserve the full existing deployment profile on first
                    # save, even when the current form edits only two fields.
                    instance = ShopConfiguration(**stored_initial(configuration()))
                changed_fields = []
                for field, value in form.cleaned_data.items():
                    if field == 'revision':
                        continue
                    if isinstance(form, (ReturnsAddressForm, CompanyForm)) and (
                            field == form.address_field and form.cleaned_data[form.same_field]):
                        continue
                    setattr(instance, field, value)
                    changed_fields.append(field)
                instance.revision = revision + 1
                instance.updated_by = request.user
                try:
                    # An absent singleton has no row to lock. A simultaneous
                    # first save must become a conflict, never overwrite it.
                    with transaction.atomic():
                        if is_new:
                            instance.save(force_insert=True)
                        else:
                            instance.save(update_fields=changed_fields + ['revision', 'updated_by', 'updated_at'])
                except IntegrityError:
                    if not is_new:
                        raise
                    _stale(form)
                    status = 409
                else:
                    LogEntry.objects.log_actions(user_id=request.user.pk,
                        queryset=ShopConfiguration.objects.filter(pk=1),
                        action_flag=ADDITION if is_new else CHANGE,
                        change_message='Réglages de la boutique : ' + section['title'] + '.')
                    messages.success(request, 'Les informations ont été enregistrées. Elles sont à jour sur le site.')
                    return redirect(section['urlname'])
    return render(request, 'boutique/settings/form.html', {
        'title': section['title'], 'section': section, 'sections': SECTIONS, 'form': form,
        'configuration': current, 'shop_configuration': current,
    }, status=status)


@catalog_required
@require_http_methods(['GET', 'POST'])
def contacts(request):
    return _edit(request, 'contacts')


@catalog_required
@require_http_methods(['GET', 'POST'])
def store(request):
    return _edit(request, 'store')


@catalog_required
@require_http_methods(['GET', 'POST'])
def returns(request):
    return _edit(request, 'returns')


@catalog_required
@require_http_methods(['GET', 'POST'])
def company(request):
    return _edit(request, 'company')
