import uuid
import hmac
import hashlib
import stripe
from django.conf import settings
from django.db.models import Prefetch, Count, Q
from django.http import HttpResponse
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from paiement.models import Order
from espace.views import private
from .access import owns_order, returns_required
from .forms import LookupForm, RequestForm, CompleteForm, ShipmentForm
from .models import ReturnRequest, ReturnShipment
from .services import submit, mark_shipped, mark_received, mark_checked, complete, add_shipment, acknowledge_shipments, ReturnError
from .mail import enqueue, deliver_pending
from .refunds import initiate, reconcile


def confidential(response):
    response['Referrer-Policy']='no-referrer'
    response['X-Robots-Tag']='noindex, nofollow'
    return response


@never_cache
@require_http_methods(['GET','POST'])
def entry(request):
    form=LookupForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        order_id=form.cleaned_data['order_id'];ip=request.META.get('REMOTE_ADDR','')
        ip_key='return-lookup-ip-'+hashlib.sha256(ip.encode()).hexdigest()
        order_key='return-lookup-order-'+str(order_id)
        allowed=cache.add(ip_key,True,30) and cache.add(order_key,True,300)
        order=Order.objects.filter(pk=order_id,status='paid').first() if allowed else None
        if order and order.customer_email and hmac.compare_digest(order.customer_email.strip().lower(),form.cleaned_data['email'].strip().lower()):
            enqueue(order,'access-'+uuid.uuid4().hex,'return_access','Utilisez ce lien privé pour consulter votre commande et déclarer un retour.')
            deliver_pending()
        messages.info(request,'Si les informations correspondent à une commande payée, un lien privé est envoyé à cette adresse. Consultez aussi les courriers indésirables.')
        return redirect('retours:entry')
    return render(request,'retours/entry.html',{'title':'Retours et rétractation','form':form,'return_address':settings.RETURN_ADDRESS,'costs_text':settings.RETURN_COSTS_TEXT})


@private
def mine(request):
    from django.core.paginator import Paginator
    records=Paginator(ReturnRequest.objects.filter(order__user=request.user).select_related('order').prefetch_related('lines','events', Prefetch('shipments', queryset=ReturnShipment.objects.defer('proof'))),20).get_page(request.GET.get('page'))
    return render(request,'retours/mine.html',{'title':'Mes retours et remboursements','records':records})


@never_cache
@require_http_methods(['GET','POST'])
def order_returns(request,pk):
    order=get_object_or_404(Order,pk=pk)
    if (order.is_demo and not settings.DEBUG) or not owns_order(request,order):raise PermissionDenied
    form=RequestForm(order,request.POST or None)
    if request.method=='POST' and form.is_valid():
        try:
            record=submit(order.pk,form.quantities(),form.cleaned_data['submission_id'],form.cleaned_data['kind'],form.cleaned_data['reason'],declarant={'name':form.cleaned_data['customer_name'],'email':form.cleaned_data['confirmation_email']})
            deliver_pending()
            messages.success(request,'Votre déclaration a été enregistrée. Un e-mail récapitule les instructions de retour. Le remboursement sera suivi dans votre dossier.')
            # Keep guest credentials only on the private order page, never in third-party links.
            token=request.GET.get('token','')
            from urllib.parse import urlencode
            url=reverse('retours:order',args=[order.pk])+(('?' + urlencode({'token':token})) if token else '')
            return confidential(redirect(url))
        except ReturnError as error:form.add_error(None,str(error))
    records=order.returns.prefetch_related('lines','events', Prefetch('shipments', queryset=ReturnShipment.objects.defer('proof')))
    return confidential(render(request,'retours/order.html',{'title':'Retour de votre commande','order':order,'form':form,'can_request':order.status in ('paid','demo') and any(r['remaining']>0 for r in form.rows),'records':records,'return_address':settings.RETURN_ADDRESS,'costs_text':settings.RETURN_COSTS_TEXT}))


@returns_required
@require_http_methods(['GET'])
def management(request):
    from django.core.paginator import Paginator
    records=ReturnRequest.objects.select_related('order').prefetch_related('lines').annotate(new_shipments=Count('shipments', filter=Q(shipments__seen_at__isnull=True)))
    from .worklist import action_definitions, open_returns
    action=request.GET.get('action','')
    action_label=''
    for key,label,condition in action_definitions():
        if action == key:
            records=open_returns().filter(condition)
            action_label=label
            break
    state=request.GET.get('etat','')
    if state in ('requested','received','completed'):records=records.filter(state=state)
    return render(request,'retours/manage.html',{'records':Paginator(records,20).get_page(request.GET.get('page')),'state':state,'action_filter':action,'action_label':action_label})


@returns_required
@require_http_methods(['GET','POST'])
def manage_detail(request,pk):
    record=get_object_or_404(ReturnRequest.objects.select_related('order').prefetch_related('lines','events', Prefetch('shipments', queryset=ReturnShipment.objects.defer('proof'))),pk=pk)
    form=CompleteForm(record,request.POST if request.POST.get('action') in ('review_complete','edit_complete') else None)
    if request.method=='POST':
        try:
            action=request.POST.get('action')
            if action=='ack_shipments':acknowledge_shipments(pk,request.user)
            elif action=='ship':mark_shipped(pk,request.user)
            elif action=='receive':mark_received(pk,request.user)
            elif action=='check':
                if request.POST.get('confirm_check') != 'yes':raise ReturnError('Confirmez avoir contrôlé les articles reçus.')
                mark_checked(pk,request.user)
            elif action=='refund':
                if request.POST.get('confirm_refund')!='yes':raise ReturnError('Confirmez explicitement le montant avant le remboursement.')
                initiate(pk,request.user,verified_problem=request.POST.get('verify_problem')=='yes')
            elif action=='reconcile':
                if record.order.is_demo:initiate(pk,request.user)
                else:
                    if not settings.STRIPE_SECRET_KEY:raise ReturnError('Stripe n’est pas configuré.')
                    reconcile(pk)
            elif action in ('review_complete','edit_complete'):
                if form.is_valid() and action == 'review_complete':
                    from .closure import prepare
                    return render(request, 'retours/confirm_complete.html', {'record':record, 'review':prepare(record, request.user, form)})
                return render(request,'retours/detail.html',{'record':record,'form':form,'is_management':True,'has_unread_shipments':record.shipments.filter(seen_at__isnull=True).exists()})
            elif action=='complete':
                if request.POST.get('confirm_completion') != 'yes':
                    raise ReturnError('Confirmez le récapitulatif avant de terminer le dossier.')
                from .closure import unpack
                received, restock, expected = unpack(request.POST.get('completion_token',''), record, request.user)
                complete(pk, request.user, received, restock, expected_snapshot=expected)
            else:raise ReturnError('Action inconnue.')
            deliver_pending()
            messages.success(request,'Dossier mis à jour. Les opérations sont conservées dans l’historique.')
            return redirect('retours:manage_detail',pk=pk)
        except ReturnError as error:messages.error(request,str(error))
        except stripe.StripeError:
            messages.error(request,'Stripe n’a pas confirmé l’opération. Le dossier est conservé : utilisez « Vérifier auprès de Stripe », sans créer un second remboursement.')
        record.refresh_from_db()
    return render(request,'retours/detail.html',{'record':record,'form':form,'is_management':True,'has_unread_shipments':record.shipments.filter(seen_at__isnull=True).exists()})


@never_cache
@require_http_methods(['GET', 'POST'])
def shipment(request, pk):
    record = get_object_or_404(ReturnRequest.objects.select_related('order'), pk=pk)
    if (record.order.is_demo and not settings.DEBUG) or not owns_order(request, record.order):
        raise PermissionDenied
    form = ShipmentForm(request.POST if request.method == 'POST' else None, request.FILES or None)
    closed = record.state == 'completed'
    if request.method == 'POST':
        if closed:
            form.add_error(None, 'Ce dossier est terminé. Contactez la boutique pour toute précision complémentaire.')
        elif form.is_valid():
            try:
                add_shipment(record.pk, form.cleaned_data['submission_id'], form.cleaned_data['carrier'],
                             form.cleaned_data['tracking_number'], form.cleaned_data['proof'])
                deliver_pending(order_id=record.order_id)
                messages.success(request, 'Votre expédition a été enregistrée. La boutique est avertie dans son espace de gestion. Le remboursement reste suivi séparément.')
                from urllib.parse import urlencode
                url = reverse('retours:order', args=[record.order_id])
                if request.GET.get('token'):
                    url += '?' + urlencode({'token':request.GET['token']})
                return confidential(redirect(url))
            except ReturnError as error:
                form.add_error(None, str(error))
    return confidential(render(request, 'retours/shipment.html', {'title':'Ajouter mon expédition', 'record':record, 'form':form, 'closed':closed}))


@never_cache
@require_http_methods(['GET'])
def proof(request, pk):
    import base64
    from .access import manager
    from connexion.email_verification import email_access_allowed
    shipment = get_object_or_404(ReturnShipment.objects.select_related('request__order').defer('proof'), pk=pk, has_proof=True)
    order = shipment.request.order
    staff = manager(request.user) and email_access_allowed(request.user)
    if (order.is_demo and not settings.DEBUG) or not (staff or owns_order(request, order)):
        raise PermissionDenied
    content = base64.b64decode(shipment.proof, validate=True)
    response = HttpResponse(content, content_type='image/webp')
    response['Content-Disposition'] = f'attachment; filename="preuve-retour-{shipment.request.short_reference}.webp"'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    response['Cache-Control'] = 'private, no-store'
    return confidential(response)
