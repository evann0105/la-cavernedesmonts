from functools import partial
from django.db import transaction
from retours.mail import enqueue, deliver_pending


def notice(cancellation, kind):
    order, refund = cancellation.order, cancellation.refund
    details = f'Commande {order.pk}\nMotif : {cancellation.reason}\n'
    details += '\n'.join(f"{line.quantity} × {line.name} — {line.size} {line.color}" for line in refund.lines.all())
    details += f'\nMontant réellement payé à rembourser, livraison comprise : {refund.display_amount:.2f} €\n'
    if kind == 'cancellation_requested':
        details += 'La commande ne sera pas expédiée. Le remboursement n’est pas encore confirmé ; un message vous informera de sa confirmation.'
    else:
        details += ('SIMULATION : aucun transfert d’argent.' if order.is_demo else 'Le remboursement est confirmé auprès du prestataire. Son apparition sur votre compte dépend des délais bancaires.')
    details += '\nAucun colis à retourner. Un code de réduction déjà utilisé reste consommé ; contactez la boutique si un nouveau geste commercial est nécessaire.'
    enqueue(order, f'{kind}-{cancellation.pk}', kind, details, request=refund)
    transaction.on_commit(partial(deliver_pending, order_id=order.pk), robust=True)
