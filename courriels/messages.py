from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

MODELS = {
    'verification': ('Confirmez votre adresse e-mail', 'Bienvenue dans votre petit coin de montagne.', 'Confirmez votre adresse e-mail pour accéder à votre compte. Ce lien expire après une heure et ne peut être utilisé qu’une fois.', 'Confirmer mon adresse'),
    'password_reset': ('Réinitialisez votre mot de passe', 'Retrouvons votre compte.', 'Vous avez demandé à choisir un nouveau mot de passe. Utilisez le lien sécurisé ci-dessous.', 'Choisir un mot de passe'),
    'order': ('Votre commande est confirmée', 'Merci pour votre confiance.', 'Votre paiement a été confirmé. Retrouvez ci-dessous le récapitulatif de votre commande.', 'Voir ma commande'),
    'shipping': ('Votre commande a été expédiée', 'Un peu de montagne arrive chez vous.', 'Votre colis a été confié au transporteur. Vous pouvez suivre son acheminement avec le lien ci-dessous.', 'Suivre mon colis'),
    'refund': ('Votre remboursement a été effectué', 'Le point sur votre commande.', 'Le remboursement a été transmis au prestataire de paiement. Son apparition sur votre compte dépend des délais bancaires.', 'Voir ma commande'),
    'return_shipment': ('Un client a expédié son retour', 'Une expédition à consulter.', 'Un client a ajouté un numéro de suivi ou un justificatif privé. Connectez-vous à la gestion pour le consulter.', 'Ouvrir le dossier'),
    'return_access': ('Votre lien de retour', 'Votre commande, à portée de main.', 'Ce lien privé permet de consulter vos dossiers et de déclarer une rétractation ou un retour.', 'Consulter ma commande et mes retours'),
    'return_requested': ('Votre demande de retour est enregistrée', 'Nous avons reçu votre déclaration.', 'Retrouvez les articles concernés et les instructions pour la suite.', 'Suivre mon retour'),
    'return_completed': ('Votre dossier de retour est terminé', 'Votre dossier est clôturé.', 'La boutique a terminé le traitement de votre retour. Votre historique reste accessible.', 'Consulter mon historique'),
    'contact': ('Nous avons reçu votre message', 'Merci de nous avoir écrit.', 'Votre message est bien arrivé. La boutique vous répondra dès que possible.', 'Découvrir la boutique'),
}


def context_for(kind, url, details='', test=False):
    subject, title, intro, action = MODELS[kind]
    return {'subject':subject, 'title':title, 'intro':intro, 'action':action, 'url':url, 'details':details, 'test':test, 'security':kind in ('verification','password_reset')}


def render_message(context):
    return (render_to_string('courriels/message.txt', context), render_to_string('courriels/message.html', context))


def build_message(kind, recipient, url, details='', test=False, withdrawal_url=''):
    context = context_for(kind, url, details, test)
    context["withdrawal_url"] = withdrawal_url
    text, html = render_message(context)
    message = EmailMultiAlternatives(('[TEST] ' if test else '') + context['subject'] + ' — La Caverne des Monts', text, settings.DEFAULT_FROM_EMAIL, [recipient])
    message.attach_alternative(html, 'text/html')
    return message
