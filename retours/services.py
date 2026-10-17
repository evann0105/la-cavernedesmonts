from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from paiement.models import Order
from stocks.models import Variant
from stocks.identity import matches_historical_label
from core.models import Product
from .models import ReturnRequest, ReturnLine, ReturnEvent
from .mail import return_notice


class ReturnError(ValueError):
    pass


def declaration_identity(order):
    """Only prefill known identity; never infer a name from the e-mail address."""
    billing = getattr(order, 'billing_details', None) or {}
    shipping = order.shipping_details or {}
    name = billing.get('name') if isinstance(billing, dict) else ''
    if not name and isinstance(shipping, dict):
        name = shipping.get('name')
    if not name and order.user_id:
        name = order.user.get_full_name()
    return {'name':' '.join(str(name or '').split()), 'email':order.customer_email or ''}


def remaining_items(order):
    used = dict(ReturnLine.objects.filter(request__order=order).values('item_index').annotate(total=Sum('quantity')).values_list('item_index','total'))
    return [{'index':n, **item, 'remaining':item['quantity']-used.get(n,0)} for n,item in enumerate(order.items)]


def allocation(order):
    if order.total_paid is None:raise ReturnError('Le montant payé doit être vérifié par la boutique.')
    weights = [int(i['unit_amount'])*int(i['quantity']) for i in order.items]
    subtotal = sum(weights)
    if subtotal <= 0:raise ReturnError('Cette commande doit être vérifiée par la boutique.')
    shipping = order.return_shipping_amount
    from paiement.line_totals import recover_line_totals, validate_totals
    from stocks.reservations import StockUnavailable
    import stripe
    try:
        if order.paid_line_totals is not None and shipping is not None:
            return validate_totals(order, order.paid_line_totals, order.total_paid, shipping), shipping
        if shipping is not None and subtotal + shipping == order.total_paid:
            return weights, shipping
        if order.is_demo:
            # Historical demonstrations have no bank data to retrieve.
            merchandise = max(0, order.total_paid - (shipping or 0))
            cumulative = 0; amounts = []
            for weight in weights:
                before = cumulative; cumulative += weight
                amounts.append(merchandise*cumulative//subtotal - merchandise*before//subtotal)
            return amounts, shipping or 0
        totals = recover_line_totals(order)
        return totals, order.return_shipping_amount
    except (StockUnavailable, stripe.StripeError) as error:
        raise ReturnError('Le détail exact des remises par article doit être vérifié avant de calculer ce remboursement. Réessayez ou contactez la boutique.') from error



@transaction.atomic
def submit(order_id, quantities, submission_id, kind='withdrawal', reason='', declarant=None):
    order = Order.objects.select_for_update().get(pk=order_id)
    previous = ReturnRequest.objects.filter(submission_id=submission_id).first()
    if previous:
        if previous.order_id != order.pk:raise ReturnError('Identifiant de demande invalide.')
        return previous
    if order.is_demo and not settings.DEBUG:raise ReturnError('Simulation indisponible hors développement.')
    if order.status != 'paid' and not order.is_demo:raise ReturnError('Cette commande n’est pas confirmée comme payée.')
    if kind not in ('withdrawal','problem'):raise ReturnError('Type de demande invalide.')
    identity = declaration_identity(order)
    if declarant is not None:
        if not isinstance(declarant, dict) or not isinstance(declarant.get('name'), str):
            raise ReturnError('Indiquez votre nom et prénom pour envoyer la déclaration.')
        name = ' '.join(declarant['name'].split())
        if not name or len(name) > 200:
            raise ReturnError('Indiquez votre nom et prénom (200 caractères maximum).')
        if declarant.get('email') != identity['email']:
            raise ReturnError('L’accusé doit être envoyé à l’adresse e-mail de la commande.')
        identity['name'] = name
    rows = remaining_items(order); amounts, shipping = allocation(order)
    selected = []
    for row in rows:
        quantity = quantities.get(row['index'],0)
        if isinstance(quantity,bool) or not isinstance(quantity,int) or quantity < 0 or quantity > row['remaining']:
            raise ReturnError('Une quantité a déjà été demandée dans un autre dossier, ou dépasse la commande.')
        if quantity:
            total_quantity = row['quantity']; used = total_quantity-row['remaining']
            amount = amounts[row['index']]*(used+quantity)//total_quantity-amounts[row['index']]*used//total_quantity
            selected.append((row,quantity,amount))
    if set(quantities)-{r['index'] for r in rows}:raise ReturnError('Article inconnu dans cette commande.')
    if not selected:raise ReturnError('Sélectionnez au moins un article à retourner.')
    # The last request that covers the entire order receives standard delivery once.
    all_claimed = sum(q for _,q,_ in selected)==sum(r['remaining'] for r in rows)
    shipping_amount = shipping if all_claimed and not order.returns.filter(shipping_amount__gt=0).exists() else 0
    record = ReturnRequest.objects.create(order=order,submission_id=submission_id,kind=kind,reason=reason[:1000],declarant=identity,amount=sum(a for _,_,a in selected)+shipping_amount,shipping_amount=shipping_amount)
    for row,quantity,amount in selected:
        ReturnLine.objects.create(request=record,item_index=row['index'],variant_id=row.get('variant'),name=row['name'],size=row.get('size',''),color=row.get('color',''),quantity=quantity,amount=amount,unit_amount=int(row['unit_amount']))
    ReturnEvent.objects.create(request=record,label='Déclaration du client enregistrée.')
    return_notice(record,'return_requested','Demande reçue le '+timezone.localtime(record.created_at).strftime('%d/%m/%Y à %H:%M'))
    return record


@transaction.atomic
def _advance_return(pk, actor, target):
    order_id = ReturnRequest.objects.values_list('order_id', flat=True).get(pk=pk)
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.is_demo and not settings.DEBUG:
        raise ReturnError('Simulation indisponible hors développement.')
    record = ReturnRequest.objects.select_for_update().get(pk=pk)
    stages = ['declared', 'shipped', 'received', 'checked']
    if record.state == 'completed' or stages.index(target) <= stages.index(record.return_state):
        return record
    if target == 'checked' and record.return_state != 'received':
        raise ReturnError('Enregistrez d’abord la réception avant de confirmer le contrôle des articles.')
    stamp = {'shipped':'shipped_at', 'received':'received_at', 'checked':'checked_at'}[target]
    record.return_state = target
    setattr(record, stamp, timezone.now())
    fields = ['return_state', stamp]
    if target == 'received':
        record.state = 'received'
        fields.append('state')
    record.save(update_fields=fields)
    labels = {
        'shipped':'Expédition du retour signalée à la boutique.',
        'received':'Retour signalé comme reçu par la boutique (sans remise en stock).',
        'checked':'Contrôle des articles confirmé par la boutique (sans remboursement ni remise en stock).',
    }
    ReturnEvent.objects.create(request=record, actor=actor, label=labels[target])
    return record


def mark_shipped(pk, actor):
    return _advance_return(pk, actor, 'shipped')


def mark_received(pk, actor):
    return _advance_return(pk, actor, 'received')


def mark_checked(pk, actor):
    return _advance_return(pk, actor, 'checked')


@transaction.atomic
def complete(pk, actor, received, restock, expected_snapshot=None):
    order_id = ReturnRequest.objects.values_list('order_id',flat=True).get(pk=pk)
    order=Order.objects.select_for_update().get(pk=order_id)
    if order.is_demo and not settings.DEBUG:raise ReturnError('Simulation indisponible hors développement.')
    record = ReturnRequest.objects.select_for_update().get(pk=pk)
    if record.state=='completed':return record
    if record.refund_state != 'succeeded':raise ReturnError('Confirmez d’abord le remboursement. Un remboursement en attente ou échoué ne peut pas être clôturé.')
    lines = list(record.lines.select_for_update().order_by('item_index'))
    products = sorted(set(Variant.objects.filter(pk__in=[i.variant_id for i in lines if i.variant_id]).values_list('product_id',flat=True)))
    list(Product.objects.select_for_update().filter(pk__in=products).order_by('pk'))
    variants = {v.pk:v for v in Variant.objects.select_for_update().filter(pk__in=[i.variant_id for i in lines if i.variant_id]).order_by('pk')}
    if expected_snapshot is not None:
        from .closure import snapshot
        if snapshot(record, lines, variants) != expected_snapshot:
            raise ReturnError('Le dossier a changé depuis le récapitulatif (quantités ou libellé du stock). Vérifiez à nouveau avant de terminer.')
    if set(received) != {i.pk for i in lines} or set(restock) != set(received):raise ReturnError('Vérifiez les quantités de chaque article.')
    for line in lines:
        got, back = received[line.pk],restock[line.pk]
        if not isinstance(got,int) or not isinstance(back,int) or not 0 <= back <= got <= line.quantity:raise ReturnError('La quantité remise en stock doit être comprise entre zéro et la quantité reçue.')
        variant = variants.get(line.variant_id)
        if back and not order.is_demo and (variant is None or variant.quantity is None):raise ReturnError('Renseignez d’abord le stock de cette variante. Aucune quantité ne peut être ajoutée à un stock inconnu.')
        if back and not order.is_demo:
            purchased = order.items[line.item_index]
            if not matches_historical_label(variant, line.size, line.color, purchased['product']):
                raise ReturnError('Cette variante a changé de taille, de couleur ou de produit depuis l’achat. Vérifiez le stock du produit retourné avant de clôturer ; aucune remise en stock n’a été effectuée.')
            variant.quantity += back;variant.revision += 1
            variant.save(update_fields=['quantity','revision','updated_at'])
        line.received=got;line.restocked=back;line.save(update_fields=['received','restocked'])
    record.state='completed';record.completed_at=timezone.now()
    record.return_state='checked'
    record.checked_at=record.checked_at or record.completed_at
    if any(received.values()):record.received_at=record.received_at or record.completed_at
    record.save(update_fields=['state','completed_at','return_state','checked_at','received_at'])
    ReturnEvent.objects.create(request=record,actor=actor,label='Dossier terminé. Quantités reçues / remises en stock : '+', '.join(f'{i.name} {i.size} {i.color} : {received[i.pk]} / {restock[i.pk]}' for i in lines))
    return_notice(record,'return_completed','La boutique a terminé votre dossier. Le remboursement est confirmé.\n' + f'Articles reçus : {sum(received.values())}\nArticles remis en stock : {sum(restock.values())}\nArticles reçus non remis en stock : {sum(received.values())-sum(restock.values())}' + ('\nSimulation : aucun stock réel n’a été modifié.' if order.is_demo else ''))
    return record


@transaction.atomic
def add_shipment(pk, submission_id, carrier='', tracking_number='', proof=None):
    import base64
    from .models import ReturnShipment
    order_id = ReturnRequest.objects.values_list('order_id', flat=True).get(pk=pk)
    order = Order.objects.select_for_update().get(pk=order_id)
    record = ReturnRequest.objects.select_for_update().get(pk=pk)
    previous = ReturnShipment.objects.filter(submission_id=submission_id).first()
    if previous:
        if previous.request_id != record.pk:
            raise ReturnError('Identifiant d’expédition invalide.')
        return previous
    if order.is_demo and not settings.DEBUG:
        raise ReturnError('Simulation indisponible hors développement.')
    if record.state == 'completed':
        raise ReturnError('Ce dossier est terminé. Contactez la boutique pour toute précision complémentaire.')
    if record.shipments.count() >= 10:
        raise ReturnError('Dix envois sont déjà enregistrés. Contactez la boutique pour toute correction supplémentaire.')
    content = proof.read(5 * 1024 * 1024 + 1) if proof else b''
    if len(content) > 5 * 1024 * 1024:
        raise ReturnError('Le justificatif ne doit pas dépasser 5 Mo.')
    if not tracking_number and not content:
        raise ReturnError('Ajoutez un numéro de suivi ou un justificatif.')
    shipment = ReturnShipment.objects.create(request=record, submission_id=submission_id,
        carrier=carrier[:100], tracking_number=tracking_number[:120],
        proof=base64.b64encode(content).decode('ascii') if content else '', has_proof=bool(content))
    if record.return_state == 'declared':
        record.return_state = 'shipped'
        record.shipped_at = timezone.now()
        record.save(update_fields=['return_state', 'shipped_at'])
    ReturnEvent.objects.create(request=record, label='Le client a ajouté une expédition'+(' avec justificatif privé.' if content else ' avec numéro de suivi.'))
    recipient = getattr(settings, 'RETURN_NOTIFICATION_EMAIL', '')
    if recipient:
        from django.urls import reverse
        from .mail import enqueue
        enqueue(order, f'shipment-{shipment.pk}', 'return_shipment',
            f'Dossier {record.short_reference} : nouvelles informations d’expédition à consulter dans la gestion. Aucun remboursement automatique n’a été déclenché.',
            request=record, recipient=recipient,
            url=settings.SITE_URL.rstrip('/') + reverse('retours:manage_detail', args=[record.pk]))
    return shipment


@transaction.atomic
def acknowledge_shipments(pk, actor):
    from .models import ReturnShipment
    record = ReturnRequest.objects.select_for_update().get(pk=pk)
    count = ReturnShipment.objects.filter(request=record, seen_at__isnull=True).update(seen_at=timezone.now())
    if count:
        ReturnEvent.objects.create(request=record, actor=actor, label=f'La boutique a consulté {count} nouvelle(s) information(s) d’expédition.')


def validate_refund_allocation(record, order):
    """Old declarations keep their history but cannot send an incorrect amount."""
    amounts, shipping = allocation(order)
    used = [0] * len(order.items)
    shipping_used = False
    for candidate in order.returns.order_by('created_at', 'pk').prefetch_related('lines'):
        expected = 0
        for line in candidate.lines.all():
            if not 0 <= line.item_index < len(used):
                raise ReturnError('Les articles de cet ancien dossier doivent être vérifiés avant remboursement.')
            index = line.item_index
            quantity = order.items[index]['quantity']
            after = used[index] + line.quantity
            if after > quantity:
                raise ReturnError('Les quantités de cet ancien dossier doivent être vérifiées avant remboursement.')
            line_amount = amounts[index]*after//quantity - amounts[index]*used[index]//quantity
            if candidate.pk == record.pk and line.amount != line_amount:
                raise ReturnError('Le montant de cet ancien dossier ne correspond pas aux remises réellement payées. Vérifiez le dossier avant remboursement ; aucun nouvel envoi n’a été effectué.')
            expected += line_amount
            used[index] = after
        all_claimed = all(used[n] == item['quantity'] for n, item in enumerate(order.items))
        expected_shipping = shipping if all_claimed and not shipping_used else 0
        shipping_used = shipping_used or expected_shipping > 0
        if candidate.pk == record.pk:
            if candidate.shipping_amount != expected_shipping or candidate.amount != expected + expected_shipping:
                raise ReturnError('Le total de cet ancien dossier doit être vérifié avant remboursement.')
            return
    raise ReturnError('Dossier de remboursement introuvable dans la commande.')
