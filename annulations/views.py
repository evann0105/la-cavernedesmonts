"""Private cancellation review. Reading the page never requests a refund."""
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from paiement.models import Order
from retours.access import returns_required

from . import services
from .forms import CancellationForm, CancellationInternalNoteForm
from .live import tracking_state
from .notifications import notification_cards, retry_notification


def _private(response):
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['Referrer-Policy'] = 'same-origin'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _load_order(pk):
    statuses = ('paid', 'demo') if settings.DEBUG else ('paid',)
    return get_object_or_404(
        Order.objects.select_related('fulfillment', 'cancellation__refund', 'cancellation__requested_by', 'cancellation__internal_note_updated_by'),
        pk=pk, status__in=statuses,
    )


def _cancellation(order):
    try:
        return order.cancellation
    except ObjectDoesNotExist:
        return None


def _recorded_summary(cancellation):
    """Use the saved refund amounts, never a fresh financial request."""
    refund = cancellation.refund
    return {
        'amount': refund.display_amount,
        'shipping_amount': Decimal(refund.shipping_amount) / 100,
        'merchandise_amount': Decimal(refund.amount - refund.shipping_amount) / 100,
        'lines': [
            {
                'name': line.name, 'size': line.size, 'color': line.color,
                'quantity': line.quantity, 'amount': line.display_amount,
                'stock_label': line.current_stock_label,
            }
            for line in refund.lines.select_related('variant', 'variant__product')
        ],
    }


@returns_required
@require_http_methods(['GET', 'POST'])
def detail(request, pk):
    order = _load_order(pk)
    cancellation = _cancellation(order)
    form = None
    note_form = None
    note_conflict = False
    refresh_confirmation = False
    preview_refreshed = False
    error = ''
    status = 200

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'confirm':
            if cancellation:
                messages.info(request, 'Cette annulation est déjà enregistrée. Retrouvez son suivi ci-dessous.')
                return _private(redirect('annulations:detail', pk=order.pk))
            form = CancellationForm(request.POST, is_demo=order.is_demo)
            if form.is_valid():
                try:
                    services.request_cancellation(
                        order_id=order.pk, actor=request.user,
                        reason=form.cleaned_data['reason'],
                        reason_code=form.cleaned_data['reason_code'],
                        internal_note=form.cleaned_data['internal_note'],
                        preview_token=form.cleaned_data['preview_token'],
                        confirmed=form.cleaned_data['confirmed'],
                    )
                except services.CancellationError as exc:
                    error = str(exc)
                    status = 400
                    refresh_confirmation = True
                else:
                    messages.success(request, 'L’annulation est enregistrée. Le suivi du remboursement et du stock figure ci-dessous.')
                    return _private(redirect('annulations:detail', pk=order.pk))
            else:
                status = 400
        elif action == 'save_note':
            if not cancellation:
                error = 'Enregistrez d’abord l’annulation pour modifier sa note interne.'
                status = 400
            else:
                note_data = request.POST.copy()
                # A separate, explicit submit button is shown after a conflict.
                # The latest revision is used only after the seller reviews it.
                if 'reviewed_revision' in request.POST:
                    note_data['revision'] = request.POST['reviewed_revision']
                note_form = CancellationInternalNoteForm(note_data)
                if note_form.is_valid():
                    try:
                        services.save_internal_note(
                            order_id=order.pk, actor=request.user,
                            note=note_form.cleaned_data['internal_note'],
                            revision=note_form.cleaned_data['revision'],
                        )
                    except services.InternalNoteConflict as exc:
                        note_conflict = True
                        note_form.add_error(None, str(exc))
                        status = 409
                    except services.CancellationError as exc:
                        note_form.add_error(None, str(exc))
                        status = 400
                    else:
                        messages.success(request, 'La note interne est enregistrée. Elle reste réservée à la boutique.')
                        return _private(redirect(reverse('annulations:detail', args=[order.pk]) + '#note-interne'))
                else:
                    status = 400
        elif action == 'retry_email':
            if not cancellation:
                error = 'Aucune annulation n’est enregistrée pour cette commande.'
                status = 400
            else:
                try:
                    notice_id = int(request.POST.get('notice_id', ''))
                    attempts = int(request.POST.get('expected_attempts', ''))
                    row = retry_notification(cancellation, request.user, notice_id, attempts)
                except (ValueError, ValidationError) as exc:
                    error = ' · '.join(exc.messages) if isinstance(exc, ValidationError) else 'Actualisez le suivi du message avant de le relancer.'
                    status = 409
                else:
                    if row['status'] == 'sent_local':
                        messages.success(request, 'Ce message est conservé dans la boîte e-mail de test. Aucun envoi au destinataire.')
                    elif row['status'] == 'sent_provider':
                        messages.success(request, 'Ce message a été pris en charge par le service d’envoi. Sa réception n’est pas confirmée.')
                    elif row['status'] == 'failed':
                        messages.error(request, 'La tentative d’envoi a échoué. Consultez le suivi de ce message ci-dessous.')
                    else:
                        messages.info(request, 'Le suivi de ce message a été actualisé : ' + row['status_label'] + '.')
                    return _private(redirect('annulations:detail', pk=order.pk))
        elif action in {'reconcile', 'restock'}:
            if not cancellation:
                error = 'Aucune annulation n’est enregistrée pour cette commande.'
                status = 400
            else:
                try:
                    if action == 'reconcile':
                        services.reconcile_cancellation(order_id=order.pk, actor=request.user)
                    else:
                        services.retry_stock(order_id=order.pk, actor=request.user)
                except services.CancellationError as exc:
                    error = str(exc)
                    status = 400
                else:
                    messages.info(request, 'Le suivi a été actualisé. Consultez le résultat ci-dessous.')
                    return _private(redirect('annulations:detail', pk=order.pk))
        else:
            error = 'Choisissez une action proposée sur cette page.'
            status = 400
        # A request may already be saved even when the refund needs attention.
        # Reload it so a failed submission never offers a second cancellation.
        order = _load_order(pk)
        cancellation = _cancellation(order)

    if cancellation:
        summary = _recorded_summary(cancellation)
        form = None
        if note_form is None:
            note_form = CancellationInternalNoteForm(initial={
                'internal_note': cancellation.internal_note,
                'revision': cancellation.internal_note_revision,
            })
    else:
        summary = services.preview(order)
        if form is None:
            form = CancellationForm(initial={'preview_token': summary['token']}, is_demo=order.is_demo)
        elif refresh_confirmation and summary['eligible']:
            # Re-review the fresh amounts without losing the seller's draft.
            # A renewed preview never carries over the previous confirmation.
            refreshed_data = form.data.copy()
            refreshed_data['preview_token'] = summary['token']
            refreshed_data.pop('confirmed', None)
            form = CancellationForm(refreshed_data, is_demo=order.is_demo)
            preview_refreshed = True

    if cancellation:
        return_url = reverse('expeditions:overview') + '?state=cancelled'
        if order.is_demo:
            return_url += '&scope=demo'
    else:
        return_url = reverse('expeditions:detail', args=[order.pk])
    # An annulled shipment detail redirects here; its parent is the list.
    request.cancellation_back_url = return_url
    notices = notification_cards(cancellation) if cancellation else ()
    movements = list(cancellation.stock_movements.select_related('actor')) if cancellation else ()
    return _private(render(request, 'annulations/detail.html', {
        'order': order, 'cancellation': cancellation, 'summary': summary,
        'form': form, 'action_error': error, 'return_url': return_url,
        'notification_cards': notices, 'stock_movements': movements,
        'note_form': note_form, 'note_conflict': note_conflict,
        'preview_refreshed': preview_refreshed,
        'retained_reason_label': dict(form.fields['reason_code'].choices).get(form['reason_code'].value(), '') if form and form['reason_code'].value() else '',
        'live_state': tracking_state(cancellation, notices, movements) if cancellation else None,
    }, status=status))
