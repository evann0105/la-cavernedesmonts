"""Private seller screens: a customer overview and a separate support workflow."""
import unicodedata
from datetime import date
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
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
from .attachments import attachment_context
from .relationships import relationship_context
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
    deadline = request.GET.get('echeance', '')
    legacy_due = not deadline and request.GET.get('relance') == '1'
    if legacy_due:
        deadline = 'due'
    if deadline not in ('', 'today', 'overdue', 'due'):
        deadline = ''
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
    today = timezone.localdate()
    if deadline:
        cases = cases.exclude(state='resolved')
        lookup = {'today': 'due_date', 'overdue': 'due_date__lt', 'due': 'due_date__lte'}[deadline]
        cases = cases.filter(**{lookup: today})
    rows = list(cases)
    if query:
        words = _normalized(query).split()
        rows = [case for case in rows if all(word in _normalized(' '.join((
            case.reference, case.customer_name, case.customer_email, case.phone, case.subject,
            str(case.order_id or '')))) for word in words)]
    # Today's callbacks and urgent requests are visible before the rest.
    rows.sort(key=lambda case: (case.state == 'resolved',
              not (case.due_date and case.due_date <= today), case.priority != 'urgent',
              case.due_date or date.max, -case.updated_at.timestamp()))
    filters = {'q': query, 'etat': state, 'priorite': priority}
    filters.update({'relance': '1'} if legacy_due else {'echeance': deadline})
    tabs = [('active', 'À suivre'), ('open', 'À traiter'), ('in_progress', 'En cours'),
            ('waiting_customer', 'En attente du client'), ('resolved', 'Résolus'), ('all', 'Tous')]
    return _private(render(request, 'clients/case_list.html', {
        'page_obj': _page(request, rows), 'query': query, 'state': state,
        'priority': priority, 'due': bool(deadline), 'deadline': deadline, 'workspace': 'sav',
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
    from .customer_picker import picker_context, picker_results, customer_payload

    is_post = request.method == 'POST'
    action = request.POST.get('picker_action', '') if is_post else ''
    key = request.POST.get('selected_client', request.GET.get('client', '')) if is_post else request.GET.get('client', '')
    if action.startswith('select:'):
        key = action[len('select:'):]
    elif action == 'new':
        key = ''
    error = None
    try:
        customer = get_customer(key) if key else None
    except Http404:
        customer = None
        error = 'Cette fiche client n’est plus disponible. Recherchez le client à nouveau avant d’enregistrer.'
    if action and action not in ('search', 'new') and not action.startswith('select:'):
        error = 'Choisissez le client avec les boutons proposés dans le formulaire.'
    orders = list(Order.objects.filter(pk__in=customer.order_ids).order_by('-created_at', '-pk')) if customer else []
    initial = {'channel': 'phone', 'category': 'other', 'priority': 'normal'}
    if is_post and action:
        # Picker actions never save a case. They preserve the unsaved request,
        # including the creation key, when JavaScript is unavailable.
        initial.update({name: request.POST.get(name, '') for name in SupportCaseForm.base_fields})
        initial['related_order'] = request.POST.get('related_order', '')
    if customer and (not is_post or action.startswith('select:')):
        payload = customer_payload(customer)
        initial.update(customer_name=payload['name'], customer_email=payload['email'], phone=payload['phone'])
        initial['related_order'] = ''
    if action == 'new':
        initial.update(customer_name='', customer_email='', phone='', related_order='')
    if customer and customer.user_id:
        initial['customer_email'] = customer.user.email
    form = SupportCaseForm(request.POST if is_post and not action else None, initial=initial)
    form.fields['related_order'] = forms.ChoiceField(label='Commande concernée', required=False,
        choices=[('', 'Sans commande précise')] + [(str(order.pk),
            f'{str(order.pk)[:8].upper()} · {timezone.localtime(order.created_at):%d/%m/%Y} · {order.display_total:.2f} €'
            + (' · SIMULATION' if order.is_demo else '')) for order in orders],
        help_text='Choisissez un client ci-dessus pour retrouver ses commandes.')
    chosen = request.GET.get('commande', '')
    if not is_post and chosen in {str(order.pk) for order in orders}:
        form.initial['related_order'] = chosen
    if customer and customer.user_id:
        form.fields['customer_email'].disabled = True
        form.fields['customer_email'].help_text = 'Adresse du compte sélectionné.'
    status = 200
    if error:
        status = 409 if is_post else 404
    elif is_post and not action:
        if form.is_valid():
            selected = next((order for order in orders if str(order.pk) == form.cleaned_data.get('related_order')), None)
            values = {name: value for name, value in form.cleaned_data.items() if name != 'related_order'}
            contact_case, contact_order = None, None
            if customer and not customer.user_id and selected is None:
                if orders:
                    contact_order = orders[-1]
                elif customer.case_ids:
                    contact_case = SupportCase.objects.filter(pk__in=customer.case_ids, user__isnull=True,
                        order__isnull=True).order_by('created_at', 'pk').first()
            try:
                if customer and not customer.user_id and not selected and not contact_order and not contact_case:
                    # A benefit retained for a deleted account must never fall
                    # back to a different account reusing the same e-mail.
                    if CustomerBenefit.objects.filter(pk__in=customer.benefit_ids).filter(
                            Q(user__isnull=False) | Q(bound_user_id__isnull=False)).exists():
                        raise ValidationError('Cette ancienne fiche ne correspond plus à un compte client actuel. Recherchez et sélectionnez la fiche actuelle du client avant de créer son dossier.')
                    if values['customer_email'] != customer.email.strip().lower():
                        raise ValidationError('Conservez l’adresse e-mail de la fiche sélectionnée, ou choisissez un autre client.')
                case, reused = create_case(request.user, values, user=customer.user if customer else None,
                    order=selected, contact_case=contact_case, contact_order=contact_order)
            except ValidationError as exc:
                _form_error(form, exc)
                status = 409
            else:
                messages.success(request, 'Ce dossier existe déjà. Vous pouvez poursuivre son suivi.' if reused else
                                 'Dossier créé. La demande est enregistrée et prête à être suivie.')
                return redirect('clients:case_detail', pk=case.pk)
        else:
            status = 400
    query = request.POST.get('client_query', '').strip()[:150] if is_post else ''
    results, more = picker_results(query) if action == 'search' else (None, False)
    context = picker_context(customer, query=query, results=results, more=more)
    context['selected_client'] = key
    return _private(render(request, 'clients/case_form.html', {
        'form': form, 'customer': customer, 'workspace': 'sav', 'picker_error': error, **context,
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
        **attachment_context(case),
        **relationship_context(case, request.user),
    }, status=status))
