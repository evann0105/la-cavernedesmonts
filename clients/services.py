"""Create and follow private SAV cases without changing purchases or sending mail."""
import json

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from connexion.email_verification import email_access_allowed
from paiement.models import Order
from retours.access import manager
from .forms import SupportCaseForm, SupportUpdateForm
from .models import SupportCase, SupportEvent


class SupportError(ValidationError):
    pass


class SupportConflict(SupportError):
    pass


def _authorize(actor):
    if not actor or not manager(actor) or not email_access_allowed(actor):
        raise PermissionDenied


def _validated(form_class, data, **kwargs):
    if not isinstance(data, dict) or set(data) - set(form_class.base_fields):
        raise SupportError('Utilisez les champs prévus pour enregistrer le dossier.')
    form = form_class(data, **kwargs)
    if not form.is_valid():
        raise SupportError(form.errors.as_data())
    return form.cleaned_data


def _reference_pk(value):
    return getattr(value, 'pk', value) if value is not None else None


def _identity(data, *, user, order):
    """Re-read identities under locks; an account binding never comes from a name."""
    user_id, order_id = _reference_pk(user), _reference_pk(order)
    order_row, user_row = None, None
    if order_id is not None:
        try:
            order_row = Order.objects.select_for_update().filter(pk=order_id).first()
        except (TypeError, ValueError, ValidationError) as exc:
            raise SupportError('Choisissez une commande valide pour ce client.') from exc
        if order_row is None:
            raise SupportConflict('Cette commande n’existe plus. Rechargez la fiche du client.')
    if user_id is not None:
        if type(user_id) is not int or user_id < 1:
            raise SupportError('Choisissez un compte client valide.')
        user_row = get_user_model().objects.select_for_update().filter(pk=user_id).first()
        if user_row is None:
            raise SupportConflict('Ce compte client n’existe plus. Rechargez la fiche du client.')
        account_email = (user_row.email or '').strip().lower()
        if data['customer_email'] != account_email:
            raise SupportConflict('L’adresse e-mail ne correspond plus au compte choisi. Rechargez la fiche du client.')
    if order_row is not None:
        if order_row.user_id is not None:
            if user_row is None or order_row.user_id != user_row.pk:
                raise SupportError('Cette commande appartient à un autre compte client.')
        else:
            order_email = (order_row.customer_email or '').strip().lower()
            if order_email and order_email != data['customer_email']:
                raise SupportError('Cette commande ne correspond pas à l’adresse e-mail du client.')
    return user_row, order_row


def _contact_reference(contact_case, *, user, order):
    """Keep an explicitly selected phone-only contact without guessing identity."""
    if contact_case is None:
        return None
    if user is not None or order is not None:
        raise SupportError('Ce contact ne peut pas être associé à un compte ou une commande différente.')
    try:
        source = SupportCase.objects.select_for_update().filter(pk=_reference_pk(contact_case)).first()
    except (TypeError, ValueError, ValidationError) as exc:
        raise SupportError('Choisissez une fiche client valide.') from exc
    if source is None:
        raise SupportConflict('Cette fiche client n’existe plus. Rechargez la liste des clients.')
    if source.user_id is not None or source.order_id is not None:
        raise SupportConflict('Cette fiche est liée à un compte ou une commande. Rechargez-la avant de créer le dossier.')
    return source.contact_reference or source.pk


def _fingerprint(data, user_id, order_id, contact_case_id=None, contact_order_id=None):
    values = {key: value for key, value in data.items() if key != 'creation_key'}
    values.update(user_id=user_id, order_id=str(order_id) if order_id is not None else None)
    if contact_case_id is not None:
        values['contact_case_id'] = str(contact_case_id)
    if contact_order_id is not None:
        values['contact_order_id'] = str(contact_order_id)
    return salted_hmac('clients-support-creation-v1',
        json.dumps(values, default=str, sort_keys=True, ensure_ascii=False), algorithm='sha256').hexdigest()


def _existing_creation(actor, creation_key, fingerprint):
    case = SupportCase.objects.filter(creation_key=creation_key).first()
    if case is None:
        return None
    if case.created_by_id != actor.pk or not constant_time_compare(case.creation_fingerprint, fingerprint):
        raise SupportConflict('Cette demande d’enregistrement a déjà été utilisée. Rechargez le formulaire pour ouvrir un nouveau dossier.')
    return case


@transaction.atomic
def create_case(actor, cleaned_data, *, user=None, order=None, contact_case=None, contact_order=None):
    """The caller supplies an explicit customer/order selection, never arbitrary POST IDs."""
    _authorize(actor)
    data = _validated(SupportCaseForm, cleaned_data)
    # The fingerprint uses explicit references even if an account is subsequently
    # removed: a repeated submission never creates a second dossier.
    if contact_order is not None and any(value is not None for value in (user, order, contact_case)):
        raise SupportError('Choisissez une seule fiche client pour cette demande.')
    fingerprint = _fingerprint(data, _reference_pk(user), _reference_pk(order),
                               _reference_pk(contact_case), _reference_pk(contact_order))
    existing = _existing_creation(actor, data['creation_key'], fingerprint)
    if existing is not None:
        return existing, True
    user_row, order_row = _identity(data, user=user, order=contact_order if contact_order is not None else order)
    contact_reference = _contact_reference(contact_case, user=user_row, order=order_row)
    if contact_order is not None:
        # Keep the explicitly chosen guest profile even when this request does
        # not concern a particular order. The source is revalidated above.
        contact_reference, order_row = order_row.pk, None
    case = SupportCase(**data, user=user_row, order=order_row, created_by=actor, updated_by=actor,
                       creation_fingerprint=fingerprint, contact_reference=contact_reference)
    case.full_clean(validate_unique=False)
    try:
        # A unique creation key serializes double clicks from separate requests.
        with transaction.atomic():
            case.save(force_insert=True)
    except IntegrityError:
        existing = _existing_creation(actor, data['creation_key'], fingerprint)
        if existing is None:
            raise
        return existing, True
    SupportEvent.objects.create(case=case, kind='created', actor=actor,
        actor_label=actor.get_username(), previous_state='', state='open', note='', changes={})
    return case, False


def _display(field, value):
    if field == 'state':
        return dict(SupportCase.STATES)[value]
    if field == 'priority':
        return dict(SupportCase.PRIORITIES)[value]
    return value.strftime('%d/%m/%Y') if value else 'Sans date prévue'


@transaction.atomic
def update_case(actor, case_id, cleaned_data):
    _authorize(actor)
    try:
        case = SupportCase.objects.select_for_update().filter(pk=case_id).first()
    except (TypeError, ValueError, ValidationError) as exc:
        raise SupportError('Ce dossier n’existe pas.') from exc
    if case is None:
        raise SupportConflict('Ce dossier n’existe plus. Revenez à la liste des dossiers.')
    data = _validated(SupportUpdateForm, cleaned_data, instance=case)
    if data['revision'] != case.revision:
        raise SupportConflict('Ce dossier a été modifié depuis votre ouverture. Rechargez-le avant d’enregistrer votre suivi.')
    changes = {}
    labels = {'state': 'État', 'priority': 'Priorité', 'due_date': 'À reprendre le'}
    previous_state = case.state
    for field, label in labels.items():
        before, after = getattr(case, field), data[field]
        if before != after:
            changes[field] = {'label': label, 'before': _display(field, before), 'after': _display(field, after)}
            setattr(case, field, after)
    if not changes and not data['note']:
        return case
    if previous_state != case.state:
        case.resolved_at = timezone.now() if case.state == 'resolved' else None
    case.updated_by = actor
    case.revision += 1
    case.full_clean()
    case.save(update_fields=['state', 'priority', 'due_date', 'resolved_at', 'updated_by', 'revision', 'updated_at'])
    SupportEvent.objects.create(case=case, kind='update' if changes else 'note', note=data['note'],
        changes=changes, actor=actor, actor_label=actor.get_username(), previous_state=previous_state, state=case.state)
    return case
