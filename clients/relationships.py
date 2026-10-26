"""Connect existing returns and benefits to a case without changing their business state."""
from django import forms
from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from avoirs.models import CustomerBenefit
from espace.models import CreditNote
from paiement.models import Order
from retours.models import ReturnRequest
from .models import SupportCase, SupportEvent, SupportRelationship
from .selectors import get_customer
from .services import SupportConflict, _authorize, _validated


def _money(value):
    return f'{value:.2f}'.replace('.', ',') + ' €'


def _order_text(order):
    return 'Commande ' + str(order.pk)[:8].upper() + (' · SIMULATION' if order.is_demo else '')


def relationship_options(case, customer=None):
    """Read current membership every time; former associations confer no access."""
    customer = customer or get_customer(f'c-{case.pk}')
    returns = ReturnRequest.objects.filter(order_id__in=customer.order_ids).select_related('order').order_by('-created_at', '-pk')
    benefits = CustomerBenefit.objects.filter(pk__in=customer.benefit_ids).prefetch_related('redemptions').order_by('-created_at', '-pk')
    credits = CreditNote.objects.filter(order_id__in=customer.order_ids).select_related('order').order_by('-issued_at', '-pk')
    groups = {'returns': [], 'benefits': [], 'credits': []}
    for record in returns:
        groups['returns'].append({
            'key': 'return:' + str(record.pk), 'pk': record.pk, 'record': record,
            'title': 'Retour ' + record.short_reference,
            'reference': _order_text(record.order),
            'status': record.dossier_status + ' · ' + record.get_refund_state_display(),
            'amount': _money(record.display_amount),
            'date': record.created_at,
            'url': reverse('retours:manage_detail', args=[record.pk]), 'action': 'Ouvrir le retour',
        })
    for record in benefits:
        groups['benefits'].append({
            'key': 'benefit:' + str(record.pk), 'pk': record.pk, 'record': record,
            'title': record.label, 'reference': record.code,
            'status': record.get_kind_display() + ' · ' + record.status_label,
            'amount': record.value_label, 'date': record.created_at,
            'url': reverse('avoirs:detail', args=[record.pk]), 'action': 'Voir le code et ses conditions',
        })
    for record in credits:
        groups['credits'].append({
            'key': 'credit:' + str(record.pk), 'pk': record.pk, 'record': record,
            'title': 'Document d’avoir ' + record.reference,
            'reference': _order_text(record.order), 'status': 'Document déjà émis',
            'amount': _money(record.amount), 'date': record.issued_at,
            'url': reverse('facturation:purchase_order', args=[record.order_id]), 'action': 'Voir la commande',
        })
    return groups


def _flatten(options):
    return {item['key']: item for entries in options.values() for item in entries}


def _label(item):
    return f"{item['title']} · {item['reference']} · {item['amount']} · {item['status']}"


class SupportRelationshipsForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    returns = forms.MultipleChoiceField(label='Retours du client', required=False, widget=forms.CheckboxSelectMultiple)
    benefits = forms.MultipleChoiceField(label='Avoirs et bons avec un code', required=False, widget=forms.CheckboxSelectMultiple)
    credits = forms.MultipleChoiceField(label='Documents d’avoir déjà émis', required=False, widget=forms.CheckboxSelectMultiple)
    reason = forms.CharField(label='Motif de cette mise à jour', max_length=1000,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Ex. : retour ouvert après cet appel, ou avoir accordé pour cette demande. Ce motif reste dans l’historique interne.')

    def __init__(self, *args, instance, options, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['revision'] = instance.revision
        active_keys = {link.target_key for link in instance.relationships.filter(removed_at__isnull=True)}
        for field, entries in options.items():
            self.fields[field].choices = [(item['key'], _label(item)) for item in entries]
            self.initial[field] = [item['key'] for item in entries if item['key'] in active_keys]


def relationship_context(case, actor):
    _authorize(actor)
    options = relationship_options(case)
    available = _flatten(options)
    active = list(case.relationships.filter(removed_at__isnull=True))
    return {
        'related_operations': [available[link.target_key] for link in active if link.target_key in available],
        'unavailable_relationship_count': sum(link.target_key not in available for link in active),
        'has_relationship_options': bool(available),
    }


def _signature(customer):
    return customer.user_id, frozenset(customer.order_ids), frozenset(customer.benefit_ids), frozenset(customer.case_ids)


@transaction.atomic
def update_relationships(actor, case_id, data):
    _authorize(actor)
    try:
        original = SupportCase.objects.get(pk=case_id)
    except (SupportCase.DoesNotExist, ValueError, TypeError, forms.ValidationError) as exc:
        raise SupportConflict('Ce dossier n’existe plus. Revenez à la liste des dossiers.') from exc
    customer = get_customer(f'c-{original.pk}')
    initial_options = relationship_options(original, customer)
    values = _validated(SupportRelationshipsForm, data, instance=original, options=initial_options)

    # Order operations already lock orders before returns/benefits. A benefit
    # edit can assign a customer FK, so lock benefits before customer accounts.
    list(Order.objects.select_for_update().filter(pk__in=customer.order_ids).order_by('pk'))
    list(CustomerBenefit.objects.select_for_update().filter(pk__in=customer.benefit_ids).order_by('pk'))
    if customer.user_id:
        list(get_user_model().objects.select_for_update().filter(pk=customer.user_id))
    list(ReturnRequest.objects.select_for_update().filter(pk__in=[item['pk'] for item in initial_options['returns']]).order_by('pk'))
    list(CreditNote.objects.select_for_update().filter(pk__in=[item['pk'] for item in initial_options['credits']]).order_by('pk'))
    case = SupportCase.objects.select_for_update().filter(pk=original.pk).first()
    if case is None or case.revision != values['revision']:
        raise SupportConflict('Le dossier a été modifié depuis son ouverture. Rechargez-le avant de modifier ses liens.')
    current_customer = get_customer(f'c-{case.pk}')
    if _signature(customer) != _signature(current_customer):
        raise SupportConflict('La fiche client a changé. Rechargez-la avant de choisir les retours et avoirs.')
    options = relationship_options(case, current_customer)
    values = _validated(SupportRelationshipsForm, data, instance=case, options=options)
    available = _flatten(options)
    desired = set(values['returns'] + values['benefits'] + values['credits'])
    existing = {link.target_key: link for link in case.relationships.select_for_update().order_by('pk')}
    active = {key for key, link in existing.items() if link.removed_at is None}
    added, removed = desired - active, active - desired
    if not added and not removed:
        return case
    now = timezone.now()
    changes = {}
    for key in sorted(removed):
        link = existing[key]
        link.removed_at, link.removed_by = now, actor
        link.save(update_fields=['removed_at', 'removed_by'])
        # Do not expose a former customer's current code or any new conditions.
        before = _label(available[key]) if key in available else 'Lien dont le rattachement client a changé'
        changes['remove_' + str(link.pk)] = {'label': 'Lien retiré', 'before': before, 'after': 'Retiré du dossier SAV'}
    fields = {'return': 'return_request_id', 'benefit': 'benefit_id', 'credit': 'credit_note_id'}
    for key in sorted(added):
        link = existing.get(key)
        if link:
            link.removed_at, link.removed_by = None, None
            link.save(update_fields=['removed_at', 'removed_by'])
        else:
            kind, target = key.split(':', 1)
            link = SupportRelationship.objects.create(case=case, created_by=actor, **{fields[kind]: target})
        changes['add_' + str(link.pk)] = {'label': 'Lien ajouté', 'before': 'Sans lien explicite', 'after': _label(available[key])}
    case.updated_by = actor
    case.revision += 1
    case.save(update_fields=['updated_by', 'revision', 'updated_at'])
    SupportEvent.objects.create(case=case, kind='update', actor=actor, actor_label=actor.get_username(),
        previous_state=case.state, state=case.state, note='Liens du dossier : ' + values['reason'], changes=changes)
    return case
