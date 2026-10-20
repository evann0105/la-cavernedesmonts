import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from config.encrypted_fields import email_digest
from retours.access import returns_required
from .forms import BenefitForm
from .mail import local_capture_enabled, notification_state
from .models import CustomerBenefit
from .services import save_benefit, notify_benefit


STATUSES = {
    'all': 'Tous', 'available': 'Disponibles', 'scheduled': 'À venir',
    'reserved': 'En cours de commande', 'used': 'Utilisés',
    'expired': 'Expirés', 'inactive': 'Désactivés',
}


def _mail_context():
    return {
        'benefit_email_capture': local_capture_enabled(),
        'benefit_mail_delivery_enabled': bool(settings.EMAIL_DELIVERY_ENABLED),
    }


def _notification_feedback(benefit):
    state = notification_state(benefit)
    if state['pending']:
        if local_capture_enabled():
            return 'Le message est en attente de capture dans la boîte e-mail de test. Aucun e-mail ne sera envoyé au client.'
        if not settings.EMAIL_DELIVERY_ENABLED:
            return 'Le message est en attente : l’envoi des e-mails est désactivé.'
        return 'Le message est en attente d’envoi. Sa réception par le client n’est pas encore confirmée.'
    if state['processed_at']:
        if state['mode'] == 'local_test':
            return 'Le message est disponible dans la boîte e-mail de test. Aucun e-mail n’a été envoyé au client.'
        if state['mode'] == 'provider':
            return 'Le message a été pris en charge par le service d’envoi. Sa réception par le client n’est pas encore confirmée.'
        return 'Un ancien traitement du message est enregistré, mais son mode d’envoi n’est pas connu. Sa réception n’est pas confirmée.'
    return 'Consultez le statut du message sur cette fiche avant de le renvoyer.'


def _customers(query):
    """Search encrypted names in Python; never send the customer directory to the browser."""
    query = query.strip()[:150]
    if len(query) < 2:
        return []
    users = get_user_model().objects.filter(is_active=True).order_by('pk')
    if '@' in query:
        return list(users.filter(email__iexact=query)[:10])
    needle = query.casefold()
    found = []
    for user in users.iterator(chunk_size=200):
        if user.email and needle in ' '.join((user.first_name, user.last_name, user.username)).casefold():
            found.append(user)
            if len(found) == 10:
                break
    return found


def _error(form, exc):
    if isinstance(exc, ValidationError):
        if hasattr(exc, 'message_dict'):
            for field, errors in exc.message_dict.items():
                for error in errors:
                    form.add_error(field if field in form.fields else None, error)
        else:
            for message in exc.messages:
                form.add_error(None, message)
    else:
        form.add_error(None, str(exc))


@returns_required
def management(request):
    query = request.GET.get('q', '').strip()[:150]
    status = request.GET.get('status', 'all')
    if status not in STATUSES:
        status = 'all'
    benefits = CustomerBenefit.objects.select_related('user').prefetch_related('redemptions').order_by('-created_at', '-pk')
    if query:
        condition = Q(code__icontains=query) | Q(label__icontains=query)
        if '@' in query:
            condition |= Q(recipient_email_hash=email_digest(query))
        else:
            hashes = [email_digest(user.email) for user in _customers(query)]
            condition |= Q(recipient_email_hash__in=hashes)
        benefits = benefits.filter(condition)
    rows = list(benefits)
    counts = {key: sum(row.status == key for row in rows) for key in STATUSES if key != 'all'}
    counts['all'] = len(rows)
    if status != 'all':
        rows = [row for row in rows if row.status == status]
    page = Paginator(rows, 24).get_page(request.GET.get('page'))
    return render(request, 'avoirs/management.html', {
        'benefits': page, 'query': query, 'status': status,
        'status_tabs': [(key, label, counts[key]) for key, label in STATUSES.items()],
        'counts': counts, **_mail_context(),
    })


@returns_required
def create(request):
    customer_query = request.GET.get('client', '').strip()[:150]
    initial = {'minimum_subtotal': '0.00', 'active': True}
    if request.GET.get('customer', '').isdigit():
        user = get_object_or_404(get_user_model(), pk=request.GET['customer'], is_active=True)
        initial['recipient_email'] = user.email
    form = BenefitForm(request.POST if request.method == 'POST' else None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            benefit = save_benefit(form.save(commit=False), actor=request.user, notify=form.cleaned_data['notify'], creation_key=form.cleaned_data.get('creation_key') or uuid.uuid4())
        except (ValidationError, ValueError) as exc:
            _error(form, exc)
        else:
            messages.success(request, 'Le code a été créé. ' + (_notification_feedback(benefit) if form.cleaned_data['notify'] else 'Aucun e-mail n’a été demandé.'))
            return redirect('avoirs:detail', pk=benefit.pk)
    return render(request, 'avoirs/form.html', {
        'form': form, 'customer_query': customer_query,
        'customers': _customers(customer_query) if customer_query else [],
        'creating': True, **_mail_context(),
    })


@returns_required
def detail(request, pk):
    benefit = get_object_or_404(CustomerBenefit.objects.select_related('user', 'created_by').prefetch_related('redemptions__order'), pk=pk)
    return render(request, 'avoirs/detail.html', {
        'benefit': benefit, 'can_edit': benefit.status not in ('reserved', 'used'),
        'redemptions': benefit.redemptions.select_related('order').order_by('-created_at'),
        'notification': notification_state(benefit), **_mail_context(),
        'events': benefit.events.select_related('actor').order_by('-created_at')[:30],
        'notification_key': uuid.uuid4(),
        'can_notify': benefit.status not in ('used', 'inactive', 'expired') and not (benefit.bound_user_id and not benefit.user_id) and (not benefit.user_id or email_digest(benefit.user.email) == benefit.recipient_email_hash),
    })


@returns_required
def edit(request, pk):
    benefit = get_object_or_404(CustomerBenefit, pk=pk)
    if benefit.status in ('reserved', 'used'):
        messages.error(request, 'Ce code est déjà utilisé ou réservé dans une commande. Ses conditions sont conservées.')
        return redirect('avoirs:detail', pk=pk)
    form = BenefitForm(request.POST if request.method == 'POST' else None, instance=benefit)
    if request.method == 'POST' and form.is_valid():
        try:
            benefit = save_benefit(form.save(commit=False), actor=request.user, notify=form.cleaned_data['notify'], reassign=form.cleaned_data.get('reassign_customer', False))
        except (ValidationError, ValueError) as exc:
            _error(form, exc)
        else:
            messages.success(request, 'Les conditions du code ont été enregistrées. ' + (_notification_feedback(benefit) if form.cleaned_data['notify'] else 'Aucun nouvel e-mail n’a été demandé.'))
            return redirect('avoirs:detail', pk=benefit.pk)
    return render(request, 'avoirs/form.html', {'form': form, 'benefit': benefit, 'creating': False, **_mail_context()})


@returns_required
@require_POST
def toggle(request, pk):
    benefit = get_object_or_404(CustomerBenefit, pk=pk)
    benefit.active = request.POST.get('active') == '1'
    try:
        save_benefit(benefit, actor=request.user, notify=False)
    except (ValidationError, ValueError) as exc:
        messages.error(request, ' '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
    else:
        messages.success(request, 'Le code est activé.' if benefit.active else 'Le code est désactivé.')
    return redirect('avoirs:detail', pk=pk)


@returns_required
@require_POST
def notify(request, pk):
    benefit = get_object_or_404(CustomerBenefit, pk=pk)
    try:
        notification_key = uuid.UUID(request.POST['notification_key']) if request.POST.get('notification_key') else uuid.uuid4()
        notify_benefit(benefit, actor=request.user, key=str(notification_key))
    except (ValidationError, ValueError) as exc:
        messages.error(request, ' '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
    else:
        messages.success(request, _notification_feedback(benefit))
    return redirect('avoirs:detail', pk=pk)
