"""Private seller screens: a customer overview and a separate support workflow."""
import unicodedata
from datetime import date
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from avoirs.models import CustomerBenefit
from espace.models import CreditNote, Voucher
from facturation.models import Invoice
from paiement.models import Order
from retours.access import returns_required
from retours.models import ReturnRequest
from .forms import SupportCaseForm, SupportUpdateForm
from .models import SupportCase
from .selectors import find_customers, get_customer
from .services import create_case, update_case


def _private(response):
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'same-origin'
    return response


def _query(request):
    return request.GET.get('q', '').strip()[:150]


def _normalized(text):
    return ''.join(char for char in unicodedata.normalize('NFKD', str(text or '').casefold())
                   if not unicodedata.combining(char))


def _page(request, rows):
    return Paginator(rows, 25).get_page(request.GET.get('page'))


@returns_required
@require_GET
def directory(request):
    query = _query(request)
    return _private(render(request, 'clients/directory.html', {
        'page_obj': _page(request, find_customers(query)), 'query': query,
        'filter_query': urlencode({'q': query}) if query else '', 'workspace': 'clients',
    }))


@returns_required
@require_GET
def profile(request, key):
    customer = get_customer(key)
    tab = request.GET.get('onglet', 'overview')
    if tab not in ('overview', 'orders', 'invoices', 'returns', 'benefits', 'cases'):
        tab = 'overview'
    orders = Order.objects.filter(pk__in=customer.order_ids).select_related('invoice', 'fulfillment').order_by('-created_at', '-pk')
    invoices = Invoice.objects.filter(order_id__in=customer.order_ids).select_related('order').order_by('-issued_at', '-pk')
    returns = ReturnRequest.objects.filter(order_id__in=customer.order_ids).select_related('order').order_by('-created_at', '-pk')
    benefits = CustomerBenefit.objects.filter(pk__in=customer.benefit_ids).prefetch_related('redemptions').order_by('-created_at', '-pk')
    credit_notes = CreditNote.objects.filter(order_id__in=customer.order_ids).select_related('order')
    vouchers = Voucher.objects.filter(user_id=customer.user_id) if customer.user_id else Voucher.objects.none()
    cases = SupportCase.objects.filter(pk__in=customer.case_ids).order_by('-updated_at', '-pk')
    tabs = [('overview', 'Vue d’ensemble', None), ('orders', 'Commandes', orders.count()),
            ('invoices', 'Factures', invoices.count()), ('returns', 'Retours', returns.count()),
            ('benefits', 'Avoirs et bons', benefits.count() + credit_notes.count() + vouchers.count()),
            ('cases', 'Dossiers SAV', cases.count())]
    source = {'orders': orders, 'invoices': invoices, 'returns': returns, 'benefits': benefits, 'cases': cases}
    return _private(render(request, 'clients/profile.html', {
        'customer': customer, 'tab': tab, 'tabs': tabs, 'workspace': 'clients',
        'page_obj': _page(request, source[tab]) if tab in source else None,
        'filter_query': urlencode({'onglet': tab}),
        'recent_orders': orders[:5] if tab == 'overview' else [],
        'recent_cases': cases[:5] if tab == 'overview' else [],
        'credit_notes': credit_notes if tab == 'benefits' else [],
        'vouchers': vouchers if tab == 'benefits' else [],
    }))


@returns_required
@require_GET
def case_list(request):
    query = _query(request)
    state = request.GET.get('etat', 'active')
    states = dict(SupportCase._meta.get_field('state').choices)
    if state not in ('active', 'all', *states):
        state = 'active'
    priority = request.GET.get('priorite', '')
    if priority not in ('', 'normal', 'urgent'):
        priority = ''
    due = request.GET.get('relance') == '1'
    cases = SupportCase.objects.select_related('user', 'order').order_by('-updated_at', '-pk')
    counts = {value: cases.filter(state=value).count() for value in states}
    counts['active'] = sum(counts[value] for value in states if value != 'resolved')
    counts['all'] = sum(counts[value] for value in states)
    if state == 'active':
        cases = cases.exclude(state='resolved')
    elif state != 'all':
        cases = cases.filter(state=state)
    if priority:
        cases = cases.filter(priority=priority)
    if due:
        cases = cases.exclude(state='resolved').filter(due_date__lte=timezone.localdate())
    rows = list(cases)
    if query:
        words = _normalized(query).split()
        rows = [case for case in rows if all(word in _normalized(' '.join((
            case.reference, case.customer_name, case.customer_email, case.phone, case.subject,
            str(case.order_id or '')))) for word in words)]
    # Today's callbacks and urgent requests are visible before the rest.
    today = timezone.localdate()
    rows.sort(key=lambda case: (case.state == 'resolved',
              not (case.due_date and case.due_date <= today), case.priority != 'urgent',
              case.due_date or date.max, -case.updated_at.timestamp()))
    filters = {'q': query, 'etat': state, 'priorite': priority, 'relance': '1' if due else ''}
    tabs = [('active', 'À suivre'), ('open', 'À traiter'), ('in_progress', 'En cours'),
            ('waiting_customer', 'En attente du client'), ('resolved', 'Résolus'), ('all', 'Tous')]
    return _private(render(request, 'clients/case_list.html', {
        'page_obj': _page(request, rows), 'query': query, 'state': state,
        'priority': priority, 'due': due, 'workspace': 'sav',
        'state_tabs': [{'value': value, 'label': label, 'count': counts[value]} for value, label in tabs],
        'filter_query': urlencode({key: value for key, value in filters.items() if value}),
    }))


def _form_error(form, error):
    if hasattr(error, 'message_dict'):
        for field, errors in error.message_dict.items():
            for message in errors:
                form.add_error(field if field in form.fields else None, message)
    else:
        for message in getattr(error, 'messages', [str(error)]):
            form.add_error(None, message)


@returns_required
@require_http_methods(['GET', 'POST'])
def case_create(request):
    # Only opaque customer references are carried in URLs. A customer selection
    # is reloaded on every POST; hidden form values never supply account IDs.
    key = request.GET.get('client', '')
    customer = get_customer(key) if key else None
    orders = list(Order.objects.filter(pk__in=customer.order_ids).order_by('-created_at', '-pk')) if customer else []
    initial = {'channel': 'phone', 'category': 'other', 'priority': 'normal'}
    if customer:
        initial.update(customer_name=customer.name,
                       customer_email=customer.user.email if customer.user_id else customer.email,
                       phone=customer.phone)
    form = SupportCaseForm(request.POST if request.method == 'POST' else None, initial=initial)
    if customer:
        form.fields['related_order'] = forms.ChoiceField(label='Commande concernée', required=False,
            choices=[('', 'Sans commande précise')] + [(str(order.pk),
                f'{str(order.pk)[:8].upper()} · {timezone.localtime(order.created_at):%d/%m/%Y} · {order.display_total:.2f} €'
                + (' · SIMULATION' if order.is_demo else '')) for order in orders])
        chosen = request.GET.get('commande', '')
        if chosen in {str(order.pk) for order in orders}:
            form.initial['related_order'] = chosen
        if customer.user_id:
            form.fields['customer_email'].disabled = True
            form.fields['customer_email'].help_text = 'Adresse du compte sélectionné.'
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            selected = next((order for order in orders if str(order.pk) == form.cleaned_data.get('related_order')), None)
            values = {key: value for key, value in form.cleaned_data.items() if key != 'related_order'}
            contact_case = None
            if customer and not customer.user_id and not orders and customer.case_ids:
                contact_case = SupportCase.objects.filter(pk__in=customer.case_ids, user__isnull=True,
                    order__isnull=True).order_by('created_at', 'pk').first()
            try:
                case, reused = create_case(request.user, values, user=customer.user if customer else None,
                                           order=selected, contact_case=contact_case)
            except ValidationError as error:
                _form_error(form, error)
                status = 409
            else:
                messages.success(request, 'Ce dossier existe déjà. Vous pouvez poursuivre son suivi.' if reused else
                                 'Dossier créé. La demande est enregistrée et prête à être suivie.')
                return redirect('clients:case_detail', pk=case.pk)
        else:
            status = 400
    return _private(render(request, 'clients/case_form.html', {
        'form': form, 'customer': customer, 'workspace': 'sav',
    }, status=status))


@returns_required
@require_http_methods(['GET', 'POST'])
def case_detail(request, pk):
    case = get_object_or_404(SupportCase.objects.select_related('user', 'order'), pk=pk)
    form = SupportUpdateForm(request.POST if request.method == 'POST' else None, instance=case)
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                update_case(request.user, case.pk, form.cleaned_data)
            except ValidationError as error:
                _form_error(form, error)
                status = 409
            else:
                messages.success(request, 'Le suivi du dossier a été enregistré.')
                return redirect('clients:case_detail', pk=case.pk)
        else:
            status = 400
    customer = get_customer(f'c-{case.pk}')
    return _private(render(request, 'clients/case_detail.html', {
        'case': case, 'customer': customer, 'form': form, 'workspace': 'sav',
        'events': case.events.select_related('actor').order_by('-created_at', '-pk'),
    }, status=status))
