"""Display the immutable refund allocation; never recalculate from catalogue prices."""
from decimal import Decimal


def euros(cents):
    return None if cents is None else Decimal(cents) / 100


def money(cents):
    return f'{euros(cents):.2f}'.replace('.', ',') + ' €'


def breakdown(record):
    lines = list(record.lines.all())
    rows = []
    gross = 0
    reductions = 0
    adjustments = 0
    verified_shipping = record.order.return_shipping_amount is not None
    known = bool(lines) and verified_shipping
    net = sum(line.amount for line in lines)
    for line in lines:
        original = line.unit_amount * line.quantity if line.unit_amount is not None else None
        reduction = max(0, original - line.amount) if original is not None and verified_shipping else None
        adjustment = max(0, line.amount - original) if original is not None and verified_shipping else None
        known = known and original is not None
        gross += original or 0
        reductions += reduction or 0
        adjustments += adjustment or 0
        rows.append({'line':line, 'unit':euros(line.unit_amount), 'gross':euros(original),
                     'reduction':euros(reduction), 'adjustment':euros(adjustment), 'net':euros(line.amount)})
    return {'rows':rows, 'known':known, 'gross':euros(gross) if known else None,
            'reductions':euros(reductions) if known else None,
            'adjustments':euros(adjustments) if known else None,
            'net':euros(net), 'shipping':euros(record.shipping_amount), 'total':euros(record.amount),
            'balanced':net + record.shipping_amount == record.amount}


def email_breakdown(record):
    data = breakdown(record)
    text = ['Détail du montant de ce dossier :']
    for row in data['rows']:
        line = row['line']
        text.append(f'{line.quantity} × {line.name} — {line.size} {line.color} : {money(line.amount)} remboursables')
        if line.unit_amount is not None and record.order.return_shipping_amount is not None:
            original = line.unit_amount * line.quantity
            text.append(f'  Avant remise : {money(original)} · Part des remises : {money(max(0, original-line.amount))}')
            if line.amount > original:
                text.append(f'  Ajustements inclus : {money(line.amount-original)}')
    text.append(f'Articles après remises et ajustements : {money(sum(line.amount for line in record.lines.all()))}')
    text.append(f'Livraison incluse dans ce remboursement : {money(record.shipping_amount)}')
    text.append(('Total remboursé : ' if record.refund_state == 'succeeded' else 'Total à rembourser : ') + money(record.amount))
    if not data['known']:
        text.append('Le détail des remises n’est pas disponible pour certains articles de cette ancienne commande.')
    if not data['balanced']:
        text.append('La ventilation de ce dossier doit être vérifiée par la boutique.')
    return '\n'.join(text)
