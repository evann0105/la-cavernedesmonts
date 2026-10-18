"""Merchant information and explicit launch checks; never a legal certification."""
import re
from html import unescape
from django.conf import settings
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.utils.html import strip_tags

VERSION = '2026-10-18.1'
DEFAULTS = {
    'business_name': 'Élodie FOLOPPE', 'legal_form': 'EI',
    'siren': '525 114 914', 'siret': '525 114 914 00019',
    'registration': '525 114 914 RCS Bourg-en-Bresse',
    'address': 'Résidence Les Gentianes, 225 rue des Monts-Jura, 01410 Lélex, France',
    'email': 'contact@la-cavernedesmonts.fr', 'phone': '04 50 40 46 17',
    'publisher': '', 'vat_notice': '', 'host_name': '', 'host_address': '', 'host_phone': '',
    'mediator_name': '', 'mediator_address': '', 'mediator_url': '',
    'delivery': '', 'return_costs': '', 'processors': '', 'guarantees_notice': '',
}
REQUIRED = {
    'business_name':'Identité juridique', 'legal_form':'Forme juridique', 'siren':'SIREN',
    'siret':'SIRET', 'registration':'Immatriculation', 'address':'Adresse professionnelle',
    'email':'Contact e-mail', 'phone':'Téléphone', 'publisher':'Responsable de publication',
    'vat_notice':'Régime / mention de TVA confirmé', 'host_name':'Nom de l’hébergeur',
    'host_address':'Adresse de l’hébergeur', 'host_phone':'Téléphone de l’hébergeur',
    'mediator_name':'Médiateur auquel la boutique adhère', 'mediator_address':'Adresse du médiateur',
    'mediator_url':'Site du médiateur', 'delivery':'Zone, transporteur, tarif et délai de livraison',
    'return_costs':'Prise en charge des frais de retour', 'processors':'Prestataires, localisation et transferts de données',
}

def short_legal_form(value):
    if isinstance(value, str) and re.fullmatch(
        r'(?:entrepreneur(?:e)?\s+individuel(?:le)?|entreprise\s+individuelle)(?:\s*\(\s*EI\s*\))?',
        value.strip(), flags=re.IGNORECASE,
    ):
        return 'EI'
    return value


def profile():
    data = {**DEFAULTS, **getattr(settings, 'SHOP_LEGAL', {})}
    data['legal_form'] = short_legal_form(data['legal_form'])
    return data


def blockers():
    data = profile()
    missing = [label for key,label in REQUIRED.items() if not isinstance(data.get(key),str) or not data[key].strip()]
    if data.get('mediator_url'):
        try: URLValidator(schemes=['https'])(data['mediator_url'])
        except ValidationError: missing.append('Adresse HTTPS valide du médiateur')
    for flag,label in [
        ('LEGAL_DOCUMENTS_APPROVED','Validation des documents par la gérante / son conseil'),
        ('LEGAL_PRIVACY_OPERATIONS_READY','Durées de conservation, purge, droits RGPD et contrats prestataires vérifiés'),
        ('LEGAL_PRODUCT_INFORMATION_READY','Composition textile, fabricant, responsable UE et avertissements vérifiés'),
        ('LEGAL_INVOICING_READY','Facturation, TVA et archivage des contrats opérationnels'),
        ('LEGAL_ACCESSIBILITY_REVIEWED','Champ d’application de l’accessibilité et parcours vérifiés'),
    ]:
        if not getattr(settings,flag,False): missing.append(label)
    from facturation.profile import blockers as invoice_blockers
    missing.extend(invoice_blockers())
    return missing


def sales_ready():
    return not blockers()


def context():
    from paiement.shipping import free_shipping_threshold
    return {'legal':profile(), 'legal_version':VERSION, 'legal_draft':not sales_ready(),
            'free_shipping_threshold':free_shipping_threshold()}


def contract_snapshot():
    ctx = context()
    pages = [('Conditions générales de vente','terms'),('Retours et formulaire type','withdrawal')]
    text_parts = []
    for title,page in pages:
        html = render_to_string('espace/legal/'+page+'_copy.html',ctx)
        html = re.sub(r'</(?:p|h[1-6]|section|div)>', '\n\n', html)
        text_parts.append(title+'\n'+unescape(strip_tags(html)))
    text = '\n\n'.join(text_parts)
    return {'version':VERSION, 'text':text}
