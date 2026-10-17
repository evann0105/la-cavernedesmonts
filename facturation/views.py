from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from connexion.email_verification import email_access_allowed
from paiement.models import Order
from retours.access import manager, owns_order, returns_required
from .models import Invoice
from .services import DocumentError, build_preview, issue
from .delivery import build_delivery_slip


def confidential(response):
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def allowed(request, order):
    return (manager(request.user) and email_access_allowed(request.user)) or owns_order(request, order) or bool(
        order.session_key and order.session_key == request.session.session_key)


def document_link(order, kind, token=''):
    url = reverse('facturation:' + kind, args=[order.pk])
    return url + ('?' + urlencode({'token': token}) if token else '')


@require_GET
def document(request, pk, kind):
    order = get_object_or_404(Order, pk=pk)
    if not allowed(request, order):
        raise Http404
    if kind == 'delivery_slip' and order.status not in ('paid', 'demo'):
        raise Http404
    invoice = Invoice.objects.filter(order=order).first() if kind == 'invoice' else None
    data = build_delivery_slip(order) if kind == 'delivery_slip' else invoice.snapshot if invoice else build_preview(order)
    is_manager = manager(request.user) and email_access_allowed(request.user)
    template = 'facturation/delivery_slip.html' if kind == 'delivery_slip' else 'facturation/document.html'
    return confidential(render(request, template, {
        'order': order, 'document': data, 'invoice': invoice, 'kind': kind,
        'is_manager': is_manager,
        'order_link': document_link(order, 'purchase_order', request.GET.get('token', '')),
        'invoice_link': document_link(order, 'invoice', request.GET.get('token', '')),
        'delivery_link': document_link(order, 'delivery_slip', request.GET.get('token', '')),
    }))


@returns_required
@require_GET
def management(request):
    orders = Order.objects.select_related('invoice').filter(status__in=('paid', 'demo')).order_by('-created_at')
    query = request.GET.get('q', '').strip()[:100]
    if query:
        orders = orders.filter(id__icontains=query)
    return render(request, 'facturation/management.html', {
        'page_obj': Paginator(orders, 25).get_page(request.GET.get('page')),
        'query': query,
    })


@returns_required
@require_POST
def issue_invoice(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if request.POST.get('confirm_issue') != 'yes':
        messages.error(request, 'Vérifiez l’aperçu et confirmez explicitement l’émission de la facture.')
    else:
        try:
            invoice = issue(order.pk, request.user)
            messages.success(request, 'Facture ' + invoice.number + ' disponible. Une réimpression conserve ce numéro.')
        except DocumentError as error:
            messages.error(request, str(error))
    return redirect('facturation:invoice', pk=order.pk)
