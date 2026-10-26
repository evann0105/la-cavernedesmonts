"""Audited corrections of a SAV request, without changing its customer identity."""
from django import forms
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from paiement.models import Order
from .forms import SupportCaseForm
from .models import SupportCase, SupportEvent
from .selectors import get_customer
from .services import SupportConflict, SupportError, _authorize, _validated


class SupportCorrectionForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    customer_name = forms.CharField(label='Nom du client', max_length=150,
        widget=forms.TextInput(attrs={'autocomplete': 'name'}))
    phone = forms.CharField(label='Téléphone', max_length=40, required=False,
        widget=forms.TextInput(attrs={'type': 'tel', 'autocomplete': 'tel'}))
    subject = forms.CharField(label='Objet de la demande', max_length=160)
    description = forms.CharField(label='Demande du client', max_length=5000,
        widget=forms.Textarea(attrs={'rows': 6}))
    channel = forms.ChoiceField(label='Demande reçue par', choices=SupportCase.CHANNELS)
    category = forms.ChoiceField(label='Type de demande', choices=SupportCase.CATEGORIES)
    related_order = forms.ChoiceField(label='Commande concernée', required=False,
        help_text='Seules les commandes de cette fiche client sont proposées.')
    reason = forms.CharField(label='Pourquoi corriger ce dossier ?', max_length=1000,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Ex. : numéro de téléphone précisé pendant le rappel. Le motif reste dans l’historique interne.')

    def __init__(self, *args, instance, orders, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        self.initial.update({name: getattr(instance, name) for name in (
            'revision', 'customer_name', 'phone', 'subject', 'description', 'channel', 'category')})
        self.initial['related_order'] = str(instance.order_id) if instance.order_id else ''
        self.fields['related_order'].choices = [('', 'Sans commande précise')] + [
            (str(order.pk), f'{str(order.pk)[:8].upper()} · {timezone.localtime(order.created_at):%d/%m/%Y}'
             f' · {order.display_total:.2f} €' + (' · SIMULATION' if order.is_demo else '')) for order in orders]

    clean_phone = SupportCaseForm.clean_phone

    def clean(self):
        cleaned = super().clean()
        if not (self.instance.customer_email or cleaned.get('phone')):
            self.add_error('phone', 'Conservez un téléphone pour pouvoir recontacter ce client sans e-mail.')
        return cleaned


def correction_orders(case, customer=None):
    customer = customer or get_customer(f'c-{case.pk}')
    order_ids = set(customer.order_ids)
    # A historical explicit link remains selectable even if the account's
    # e-mail changed. Keeping that existing link never reassigns the case.
    if case.order_id:
        order_ids.add(case.order_id)
    return list(Order.objects.filter(pk__in=order_ids).order_by('-created_at', '-pk'))


def _group_signature(customer):
    return customer.user_id, frozenset(customer.order_ids), frozenset(customer.case_ids)


def _order_label(order_id):
    return f'Commande {str(order_id)[:8].upper()}' if order_id else 'Sans commande précise'


@transaction.atomic
def correct_case(actor, case_id, cleaned_data):
    _authorize(actor)
    try:
        original = SupportCase.objects.get(pk=case_id)
    except (SupportCase.DoesNotExist, ValueError, TypeError, forms.ValidationError) as exc:
        raise SupportConflict('Ce dossier n’existe plus. Revenez à la liste des dossiers.') from exc
    customer = get_customer(f'c-{original.pk}')
    orders = correction_orders(original, customer)
    data = _validated(SupportCorrectionForm, cleaned_data, instance=original, orders=orders)

    # Use the same deterministic order for all corrections to a shared profile.
    # The second directory read validates membership after the related records
    # have been locked; encrypted addresses are never used in SQL predicates.
    user_ids = {value for value in (original.user_id, customer.user_id) if value is not None}
    locked_orders = {str(order.pk): order for order in Order.objects.select_for_update()
                     .filter(pk__in=[order.pk for order in orders]).order_by('pk')}
    list(get_user_model().objects.select_for_update().filter(pk__in=user_ids).order_by('pk'))
    locked_cases = {case.pk: case for case in SupportCase.objects.select_for_update()
                    .filter(pk__in=customer.case_ids).order_by('pk')}
    case = locked_cases.get(original.pk)
    if case is None:
        raise SupportConflict('Ce dossier n’existe plus. Revenez à la liste des dossiers.')
    if case.revision != data['revision']:
        raise SupportConflict('Ce dossier a été modifié depuis votre ouverture. Rechargez-le avant de corriger ses informations.')
    current_customer = get_customer(f'c-{case.pk}')
    if _group_signature(customer) != _group_signature(current_customer):
        raise SupportConflict('La fiche client a changé. Rechargez-la avant de choisir sa commande.')
    # Rerun validation against the locked request and order rows, including the
    # phone requirement if a historical case changed during form preparation.
    data = _validated(SupportCorrectionForm, cleaned_data, instance=case, orders=list(locked_orders.values()))
    selected = locked_orders.get(data['related_order']) if data['related_order'] else None
    if data['related_order'] and selected is None:
        raise SupportConflict('Cette commande n’est plus disponible. Rechargez la fiche client.')
    if selected and selected.pk != case.order_id:
        if selected.pk not in current_customer.order_ids:
            raise SupportError('Choisissez une commande de cette fiche client.')
        if selected.user_id is not None and selected.user_id != current_customer.user_id:
            raise SupportError('Cette commande appartient à un autre compte client.')
        if case.user_id is not None and selected.user_id not in (None, case.user_id):
            raise SupportError('Cette commande appartient à un autre compte client.')

    changes = {}
    fields = {'customer_name': 'Nom du client', 'phone': 'Téléphone', 'subject': 'Objet',
              'description': 'Demande du client', 'channel': 'Canal', 'category': 'Type de demande'}
    choices = {'channel': dict(SupportCase.CHANNELS), 'category': dict(SupportCase.CATEGORIES)}
    for field, label in fields.items():
        before, after = getattr(case, field), data[field]
        if before != after:
            changes[field] = {'label': label, 'before': choices.get(field, {}).get(before, before) or 'Non renseigné',
                              'after': choices.get(field, {}).get(after, after) or 'Non renseigné'}
            setattr(case, field, after)
    selected_id = selected.pk if selected else None
    if selected_id != case.order_id:
        changes['order'] = {'label': 'Commande concernée', 'before': _order_label(case.order_id),
                            'after': _order_label(selected_id)}
        if case.user_id is None and case.order_id:
            # Keep a valid prior contact anchor. Otherwise remember the old
            # order as the profile anchor independently from the request's
            # current order; directory selectors resolve either kind of UUID.
            reference = case.contact_reference
            canonical = reference and reference != case.pk and (
                f'c-{reference}' in current_customer.aliases or f'o-{reference}' in current_customer.aliases)
            if not canonical:
                case.contact_reference = case.order_id
        case.order = selected
    if not changes:
        return case
    case.revision += 1
    case.updated_by = actor
    case.full_clean()
    case.save(update_fields=[*fields, 'order', 'contact_reference', 'revision', 'updated_by', 'updated_at'])
    # Verify the final grouping too, so a reference cannot silently move this
    # request to another person's profile after a relationship is removed.
    final_customer = get_customer(f'c-{case.pk}')
    if final_customer.key != current_customer.key:
        raise SupportConflict('Cette correction modifierait le rattachement du client. La modification n’a pas été enregistrée.')
    SupportEvent.objects.create(case=case, kind='update', actor=actor, actor_label=actor.get_username(),
        previous_state=case.state, state=case.state, note='Correction du dossier : ' + data['reason'], changes=changes)
    return case
