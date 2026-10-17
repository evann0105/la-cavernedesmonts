"""Read-only subject exports and retention review, with explicit data boundaries.

This module intentionally contains no account deletion or automatic financial-data
purge. Retention review candidates need a human check for ongoing claims and the
actual end of each legal retention period.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db.models import Max, Prefetch, Q
from django.utils import timezone

from connexion.models import SignupQuota
from facturation.models import Invoice
from paiement.models import Order
from retours.models import ReturnMail, ReturnRequest, ReturnShipment
from .models import Address, CreditNote, Voucher


VERSION = '2026-10-16'
ITEM_FIELDS = ('product', 'variant', 'name', 'size', 'color', 'quantity', 'unit_amount', 'image')


def subject_export(user):
    """Export only records linked to this account, never inferred by e-mail.

    Account identifiers, passwords, token digests, OTP seeds, session secrets,
    checkout payloads and mail access links are not customer export fields.
    Guests and external providers are handled through the documented rights
    procedure, with identity verification before any disclosure.
    """
    if not user.is_authenticated or not user.is_active:
        raise ValueError('Un compte actif authentifié est nécessaire.')
    orders = list(Order.objects.filter(user_id=user.pk).order_by('created_at', 'pk'))
    invoices = {invoice.order_id: invoice for invoice in Invoice.objects.filter(
        order_id__in=[order.pk for order in orders]).only('order_id', 'number', 'issued_at', 'snapshot')}
    records = []
    for order in orders:
        returns = []
        for record in order.returns.prefetch_related('lines', 'events', Prefetch('shipments', queryset=ReturnShipment.objects.defer('proof'))).order_by('created_at', 'pk'):
            returns.append({
                'reference': str(record.pk), 'type': record.get_kind_display(),
                'dossier': record.get_state_display(), 'retour': record.get_return_state_display(),
                'remboursement': record.get_refund_state_display(), 'motif': record.reason,
                'declarant': getattr(record, 'declarant', {}),
                'montant_centimes': record.amount, 'livraison_centimes': record.shipping_amount,
                'declare_le': record.created_at, 'expedie_le': record.shipped_at,
                'recu_le': record.received_at, 'controle_le': record.checked_at,
                'rembourse_le': record.refunded_at, 'termine_le': record.completed_at,
                'articles': [{'nom': line.name, 'taille': line.size, 'couleur': line.color,
                              'quantite': line.quantity, 'recu': line.received, 'remis_en_stock': line.restocked,
                              'montant_centimes': line.amount} for line in record.lines.all()],
                'expeditions': [{'reference': str(shipment.pk), 'transporteur': shipment.carrier,
                                'suivi': shipment.tracking_number, 'ajoute_le': shipment.created_at,
                                'justificatif_disponible': shipment.has_proof}
                               for shipment in record.shipments.all()],
                'historique': [{'date': event.created_at, 'evenement': event.label} for event in record.events.all()],
            })
        records.append({
            'reference': str(order.pk), 'statut': order.get_status_display(),
            'cree_le': order.created_at, 'paye_le': order.paid_at,
            'email_commande': order.customer_email, 'livraison': order.shipping_details,
            'facturation': getattr(order, 'billing_details', {}),
            'facture_emise': ({'numero': invoices[order.pk].number, 'emise_le': invoices[order.pk].issued_at,
                               'document': invoices[order.pk].snapshot} if order.pk in invoices else None),
            'sous_total_centimes': order.subtotal, 'paye_centimes': order.total_paid,
            'montants_articles_apres_remise_centimes': order.paid_line_totals,
            'livraison_standard_centimes': order.return_shipping_amount,
            'articles': [{key: item[key] for key in ITEM_FIELDS if key in item}
                         for item in order.items if isinstance(item, dict)],
            'conditions_acceptees_le': order.terms_accepted_at,
            'conditions_contractuelles': order.terms_snapshot,
            'retours': returns,
        })
    return {
        'version': VERSION, 'exporte_le': timezone.now(),
        'perimetre': 'Données du compte et commandes qui lui sont rattachées. Les achats invités et les données détenues par les prestataires sont à demander séparément après vérification.',
        'pieces_jointes': 'Les justificatifs privés restent téléchargeables dans le dossier de retour. Pour les autres données ou documents absents de cet export, contactez la boutique.',
        'compte': {'identifiant': user.username, 'email': user.email, 'prenom': user.first_name,
                   'nom': user.last_name, 'inscrit_le': user.date_joined, 'derniere_connexion': user.last_login},
        'adresses': [{'libelle': address.label, 'destinataire': address.recipient, 'ligne1': address.line1,
                      'ligne2': address.line2, 'code_postal': address.postal_code, 'ville': address.city,
                      'pays': address.country} for address in Address.objects.filter(user_id=user.pk)],
        'commandes': records,
        'avoirs_enregistres': [{'commande': str(credit.order_id), 'reference': credit.reference,
                               'montant_euros': credit.amount, 'motif': credit.reason, 'emis_le': credit.issued_at}
                              for credit in CreditNote.objects.filter(order__user_id=user.pk)],
        'bons_de_reduction': [{'code': voucher.code, 'conditions': voucher.description,
                              'expire_le': voucher.expires_at, 'actif': voucher.active}
                             for voucher in Voucher.objects.filter(user_id=user.pk)],
    }


def _years_before(now, years):
    try:
        return now.replace(year=now.year - years)
    except ValueError:  # February 29 in a non-leap target year.
        return now.replace(year=now.year - years, day=28)


def retention_review(now=None):
    """Counts only. No identifiers, personal payloads, deletion or anonymisation."""
    now = now or timezone.now()
    inactive_before = _years_before(now, 2)
    users = get_user_model().objects.filter(is_staff=False, is_superuser=False).annotate(
        last_order=Max('orders__created_at'), last_return=Max('orders__returns__created_at'),
        last_shipment=Max('orders__returns__shipments__created_at'),
    ).filter(date_joined__lt=inactive_before).filter(
        Q(last_login__isnull=True) | Q(last_login__lt=inactive_before)
    ).filter(Q(last_order__isnull=True) | Q(last_order__lt=inactive_before)).filter(
        Q(last_return__isnull=True) | Q(last_return__lt=inactive_before)
    ).filter(Q(last_shipment__isnull=True) | Q(last_shipment__lt=inactive_before))
    stale_orders = Order.objects.filter(status__in=('pending', 'expired'),
        created_at__lt=now - timedelta(days=90), returns__isnull=True).filter(
        Q(reservation_expires_at__isnull=True) | Q(reservation_expires_at__lte=now))
    entries = [
        ('expired_sessions', Session.objects.filter(expire_date__lt=now).count(),
         'Sessions expirées', 'Nettoyage technique possible après vérification de la configuration de session.'),
        ('expired_security_quotas', SignupQuota.objects.filter(expires_at__lt=now - timedelta(days=1)).count(),
         'Compteurs de sécurité expirés depuis plus de 24 heures', 'Ne pas retirer les limitations encore actives.'),
        ('inactive_accounts_review', users.count(), 'Comptes sans activité identifiée depuis deux ans',
         'Revue manuelle, vérifier les contacts récents, notifier avant fermeture et distinguer compte, contrat et archive légale. Les modifications de profil ne sont pas horodatées par ce modèle.'),
        ('abandoned_orders_review', stale_orders.count(), 'Commandes non payées anciennes de plus de 90 jours',
         'Réconcilier le paiement chez Stripe, les réservations et tout litige avant une décision de suppression.'),
        ('demo_orders_review', Order.objects.filter(status='demo', created_at__lt=now - timedelta(days=90)).count(),
         'Simulations anciennes de plus de 90 jours', 'Vérifier le périmètre de test et les relations protégées avant suppression.'),
        ('return_proofs_review', ReturnShipment.objects.filter(has_proof=True,
         request__state='completed', request__completed_at__lt=now - timedelta(days=90)).count(),
         'Justificatifs de dossiers clos depuis plus de 90 jours',
         'Revoir les pièces superflues ; conserver séparément les preuves encore nécessaires à un remboursement, une garantie ou un litige.'),
        ('sent_mails_review', ReturnMail.objects.filter(sent_at__lt=now - timedelta(days=90)).count(),
         'Copies techniques de courriels envoyés depuis plus de 90 jours',
         'Conserver les confirmations contractuelles et preuves utiles dans l’archive requise avant de retirer les copies techniques ; ne pas supprimer les messages non envoyés.'),
        ('old_paid_orders_review', Order.objects.filter(status='paid', created_at__lt=_years_before(now, 10)).count(),
         'Commandes payées créées il y a plus de dix ans',
         'Ce seuil déclenche seulement une revue : calculer le terme exact depuis livraison / prestation ou clôture de l’exercice comptable selon la pièce ; suspendre en cas de litige.'),
    ]
    return {'version': VERSION, 'controle_le': now, 'mode': 'lecture seule', 'modifications': 0,
            'attention': 'Ces nombres ne sont pas des autorisations de suppression. Les archives, journaux du serveur, messagerie externe et sauvegardes nécessitent un contrôle séparé.',
            'categories': [{'code': code, 'nombre': count, 'libelle': label, 'action': action}
                           for code, count, label, action in entries]}
