"""Read-only seller directory built from the identities already on each record.

Personal data is decrypted only for the authorized request. Nothing is persisted
or reassigned: an explicit account attachment always wins over an e-mail match.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal
import re
import unicodedata

from django.contrib.auth import get_user_model
from django.http import Http404

from avoirs.models import CustomerBenefit
from paiement.models import Order
from .models import SupportCase


_KEY = re.compile(r'(?:u|b)-[1-9][0-9]*|(?:o|c)-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
_EPOCH = datetime.min.replace(tzinfo=datetime_timezone.utc)


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _email(value):
    return _text(value).lower()


def _fold(value):
    normalized = unicodedata.normalize('NFKD', _text(value).casefold())
    return ' '.join(''.join(character for character in normalized if not unicodedata.combining(character)).split())


def _contact(details, key):
    return _text(details.get(key)) if isinstance(details, dict) else ''


@dataclass
class CustomerSummary:
    key: str
    name: str = ''
    email: str = ''
    phone: str = ''
    user: object = None
    user_id: int = None
    order_ids: list = field(default_factory=list)
    benefit_ids: list = field(default_factory=list)
    case_ids: list = field(default_factory=list)
    last_activity: datetime = None
    paid_count: int = 0
    paid_total: Decimal = Decimal('0.00')
    paid_unknown_count: int = 0
    demo_count: int = 0
    open_case_count: int = 0
    aliases: set = field(default_factory=set, repr=False)
    search_text: str = field(default='', repr=False)
    _terms: set = field(default_factory=set, repr=False)
    _name_rank: int = field(default=0, repr=False)
    _email_rank: int = field(default=0, repr=False)
    _phone_rank: int = field(default=0, repr=False)

    @property
    def order_count(self):
        return len(self.order_ids)

    @property
    def account_label(self):
        if self.user is None:
            return 'Sans compte en ligne'
        return 'Compte client' if self.user.is_active else 'Compte désactivé'

    def absorb(self, *, alias, name='', email='', phone='', at=None,
               name_rank=1, email_rank=1, phone_rank=1, terms=()):
        self.aliases.add(alias)
        for attribute, value, rank in [('name', name, name_rank), ('email', email, email_rank), ('phone', phone, phone_rank)]:
            value = _text(value)
            rank_attribute = '_' + attribute + '_rank'
            if value and (not getattr(self, attribute) or rank >= getattr(self, rank_attribute)):
                setattr(self, attribute, value)
                setattr(self, rank_attribute, rank)
        self._terms.update(_text(value) for value in (name, email, phone, *terms) if _text(value))
        if at is not None and (self.last_activity is None or at > self.last_activity):
            self.last_activity = at


def load_directory():
    """Return grouped summaries, newest activity first, without a database write.

    All account-bound orders/codes/cases stay with that account even when its
    address changes. Only unbound records may join by an exact normalized e-mail.
    Homonyms, empty e-mails and phone numbers never cause an identity merge.
    """
    groups = {}
    email_users = {}
    existing_user_ids = set()
    order_groups = {}
    case_groups = {}

    def group_for(*, alias, email='', user_id=None, retained_user_id=None, order_id=None,
                  contact_reference=None, case_id=None):
        if user_id is not None:
            identity = ('user', user_id)
        elif retained_user_id is not None:
            # A code assigned to a deleted account cannot be transferred to a
            # newly created account which happens to reuse its old address.
            identity = ('former-user', retained_user_id)
        elif order_id is not None and order_id in order_groups:
            identity = order_groups[order_id]
        elif contact_reference is not None:
            # The seller explicitly selected this existing contact. Following
            # that source preserves a phone-only identity even if the next
            # request supplies a new e-mail address. Sources precede their
            # follow-ups in creation order, including e-mail-based guests.
            # A corrected guest case can retain its original order as a stable
            # contact anchor after the seller removes the order association.
            identity = case_groups.get(contact_reference,
                order_groups.get(contact_reference, ('contact', contact_reference)))
        elif _email(email):
            identity = ('user', email_users[_email(email)]) if _email(email) in email_users else ('email', _email(email))
        elif case_id is not None:
            identity = ('contact', case_id)
        else:
            identity = ('record', alias)
        if identity not in groups:
            groups[identity] = CustomerSummary(key=alias)
        groups[identity].aliases.add(alias)
        return identity, groups[identity]

    users = get_user_model().objects.only('pk', 'username', 'first_name', 'last_name', 'email', 'is_active', 'date_joined', 'last_login').order_by('pk')
    for user in users.iterator(chunk_size=500):
        alias = f'u-{user.pk}'
        _, row = group_for(alias=alias, user_id=user.pk)
        row.user, row.user_id = user, user.pk
        existing_user_ids.add(user.pk)
        full_name = ' '.join(part for part in (_text(user.first_name), _text(user.last_name)) if part)
        row.absorb(alias=alias, name=full_name or user.username, email=user.email,
                   at=max(value for value in (user.date_joined, user.last_login) if value is not None),
                   name_rank=5 if full_name else 0, email_rank=5, terms=(user.username,))
        if _email(user.email):
            email_users[_email(user.email)] = user.pk

    orders = Order.objects.only('pk', 'user_id', 'customer_email', 'billing_details', 'shipping_details',
                                'created_at', 'paid_at', 'status', 'total_paid').order_by('created_at', 'pk')
    for order in orders.iterator(chunk_size=500):
        alias = f'o-{order.pk}'
        identity, row = group_for(alias=alias, email=order.customer_email, user_id=order.user_id)
        order_groups[order.pk] = identity
        buyer = _contact(order.billing_details, 'name')
        recipient = _contact(order.shipping_details, 'name')
        phone = _contact(order.billing_details, 'phone') or _contact(order.shipping_details, 'phone')
        row.absorb(alias=alias, name=buyer or recipient, email=order.customer_email, phone=phone,
                   name_rank=3 if buyer else 2, email_rank=2, phone_rank=2,
                   at=order.paid_at or order.created_at, terms=(recipient,))
        row.order_ids.append(order.pk)
        if order.status == 'paid':
            row.paid_count += 1
            if order.total_paid is None:
                row.paid_unknown_count += 1
            else:
                row.paid_total += Decimal(order.total_paid) / 100
        elif order.status == 'demo':
            row.demo_count += 1

    benefits = CustomerBenefit.objects.only('pk', 'user_id', 'bound_user_id', 'recipient_email', 'created_at', 'updated_at').order_by('created_at', 'pk')
    for benefit in benefits.iterator(chunk_size=500):
        alias = f'b-{benefit.pk}'
        # A conflicting explicit FK and retained assignment is treated as a
        # separate record, rather than choosing an identity opportunistically.
        conflicting = benefit.user_id is not None and benefit.bound_user_id not in (None, benefit.user_id)
        if conflicting:
            identity = ('record', alias)
            row = groups.setdefault(identity, CustomerSummary(key=alias))
        else:
            retained = benefit.bound_user_id if benefit.user_id is None else None
            if retained in existing_user_ids:
                # The stable binding is sufficient when the original account
                # still exists, even if the nullable relationship is missing.
                _, row = group_for(alias=alias, user_id=retained)
            else:
                _, row = group_for(alias=alias, email=benefit.recipient_email,
                                   user_id=benefit.user_id, retained_user_id=retained)
        row.absorb(alias=alias, email=benefit.recipient_email, at=benefit.updated_at or benefit.created_at)
        row.benefit_ids.append(benefit.pk)

    cases = SupportCase.objects.only('pk', 'user_id', 'order_id', 'contact_reference', 'customer_name', 'customer_email', 'phone',
                                     'state', 'created_at', 'updated_at').order_by('created_at', 'pk')
    for case in cases.iterator(chunk_size=500):
        alias = f'c-{case.pk}'
        identity, row = group_for(alias=alias, email=case.customer_email, user_id=case.user_id,
                                 order_id=case.order_id, contact_reference=case.contact_reference, case_id=case.pk)
        case_groups[case.pk] = identity
        row.absorb(alias=alias, name=case.customer_name, email=case.customer_email, phone=case.phone,
                   name_rank=4, email_rank=3, phone_rank=3, at=case.updated_at or case.created_at)
        row.case_ids.append(case.pk)
        if case.state != 'resolved':
            row.open_case_count += 1

    rows = list(groups.values())
    for row in rows:
        row.name = row.name or row.email or 'Client sans nom renseigné'
        row.search_text = _fold(' '.join(row._terms))
        row.order_ids.reverse()
        row.benefit_ids.reverse()
        row.case_ids.reverse()
    rows.sort(key=lambda row: (_fold(row.name), row.key))
    rows.sort(key=lambda row: row.last_activity or _EPOCH, reverse=True)
    return rows


def find_customers(query=''):
    """Search partial names/e-mails, including names captured with old orders."""
    words = _fold(query[:200] if isinstance(query, str) else '').split()
    rows = load_directory()
    return [row for row in rows if all(word in row.search_text for word in words)] if words else rows


def get_customer(key):
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise Http404('Cette fiche client n’existe pas.')
    key = key.lower()
    for row in load_directory():
        if key in row.aliases:
            return row
    raise Http404('Cette fiche client n’existe pas.')
