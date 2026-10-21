"""Current delivery settings and immutable, JSON-safe checkout quotations."""
from decimal import Decimal, InvalidOperation
from django.conf import settings

from .shipping_countries import COUNTRY_CHOICES


COUNTRY_LABELS = dict(COUNTRY_CHOICES)
POLICY_FIELDS = ('enabled', 'free_shipping_enabled', 'free_shipping_threshold',
                 'preparation_days_min', 'preparation_days_max', 'carrier_name',
                 'service_name', 'carrier_website', 'public_notice')


class ShippingUnavailable(ValueError):
    pass


def legacy_threshold():
    try:
        amount = Decimal(str(getattr(settings, 'FREE_SHIPPING_THRESHOLD_EUR', '99.00')))
        if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal('.01')):
            raise InvalidOperation
        return amount
    except (InvalidOperation, TypeError, ValueError):
        raise ShippingUnavailable('Le seuil de livraison offerte doit être un montant positif ou nul, avec deux décimales au maximum.')


def _days_ready(minimum, maximum):
    return (type(minimum) is int and type(maximum) is int
            and 0 <= minimum <= maximum <= 60)


def _zone_ready(zone):
    price = zone.get('price')
    return (zone.get('active') is True and isinstance(price, Decimal) and price.is_finite()
            and price >= 0 and price == price.quantize(Decimal('.01'))
            and _days_ready(zone.get('delivery_days_min'), zone.get('delivery_days_max'))
            and bool(zone.get('countries'))
            and all(code in COUNTRY_LABELS for code in zone['countries']))


def configuration(instance=None):
    """Read only. The first merchant save opts in to managed shipping."""
    from .models import ShippingConfiguration
    if instance is None:
        instance = ShippingConfiguration.objects.select_related('updated_by').prefetch_related('zones__countries').filter(pk=1).first()
    if instance is None:
        data = dict(enabled=True, free_shipping_enabled=True,
                    free_shipping_threshold=legacy_threshold(), preparation_days_min=None,
                    preparation_days_max=None, carrier_name='', service_name='Livraison standard',
                    carrier_website='', public_notice='', revision=0,
                    updated_at=None, updated_by=None, zones=[], managed=False)
    else:
        data = {field: getattr(instance, field) for field in POLICY_FIELDS}
        data.update(managed=True, revision=instance.revision, updated_at=instance.updated_at,
                    updated_by=instance.updated_by, zones=[])
        for zone in instance.zones.all():
            countries = sorted(country.code for country in zone.countries.all())
            labels = [COUNTRY_LABELS.get(code, code) for code in countries]
            entry = {field: getattr(zone, field) for field in
                     ('title', 'price', 'free_shipping_eligible', 'delivery_days_min',
                      'delivery_days_max', 'active', 'revision')}
            entry.update(pk=zone.pk, id=zone.pk, countries=countries, country_labels=labels,
                         country_summary=', '.join(labels))
            entry['ready'] = _zone_ready(entry)
            data['zones'].append(entry)
    pairs = [(code, COUNTRY_LABELS[code]) for zone in data['zones'] if _zone_ready(zone)
             for code in zone['countries']]
    data['countries'] = sorted(pairs, key=lambda pair: pair[1].casefold())
    data['ready'] = ready(data)
    data['blockers'] = blockers(data)
    return data


def ready(config=None):
    config = configuration() if config is None else config
    return bool(config.get('managed') and config.get('enabled')
                and str(config.get('carrier_name') or '').strip()
                and str(config.get('service_name') or '').strip()
                and _days_ready(config.get('preparation_days_min'), config.get('preparation_days_max'))
                and any(_zone_ready(zone) for zone in config.get('zones', [])))


def blockers(config=None):
    config = configuration() if config is None else config
    errors = []
    if not config.get('managed'):
        errors.append('Enregistrez vos réglages de livraison pour préparer les tarifs et destinations.')
    if not config.get('enabled'):
        errors.append('La livraison en ligne est désactivée.')
    if not str(config.get('carrier_name') or '').strip():
        errors.append('Le transporteur reste à renseigner.')
    if not _days_ready(config.get('preparation_days_min'), config.get('preparation_days_max')):
        errors.append('Les délais minimum et maximum de préparation restent à compléter.')
    if not any(_zone_ready(zone) for zone in config.get('zones', [])):
        errors.append('Activez au moins une zone avec ses pays, son tarif et ses délais de livraison.')
    return errors


def quote(subtotal_cents, country, config=None):
    config = configuration() if config is None else config
    if type(subtotal_cents) is not int or subtotal_cents < 0:
        raise ShippingUnavailable('Le montant des articles doit être vérifié avant de calculer la livraison.')
    if not ready(config):
        raise ShippingUnavailable('La livraison n’est pas encore disponible. Contactez la boutique pour préparer votre commande.')
    country = str(country or '').strip().upper()
    zones = [zone for zone in config['zones'] if country in zone['countries'] and _zone_ready(zone)]
    if country not in COUNTRY_LABELS or len(zones) != 1:
        raise ShippingUnavailable('Cette destination n’est pas disponible pour la livraison. Choisissez un pays proposé.')
    zone = zones[0]
    eligible = bool(config['free_shipping_enabled'] and zone['free_shipping_eligible'])
    threshold = int(config['free_shipping_threshold'] * 100) if eligible else None
    is_free = eligible and subtotal_cents >= threshold
    amount = 0 if is_free else int(zone['price'] * 100)
    return {
        'managed': True, 'version': 1, 'country': country, 'country_label': COUNTRY_LABELS[country],
        'zone_id': zone['pk'], 'zone_name': zone['title'], 'amount': amount, 'threshold': threshold,
        'free_eligible': eligible, 'is_free': bool(is_free),
        'service_name': config['service_name'], 'carrier_name': config['carrier_name'],
        'public_notice': config['public_notice'],
        'preparation_min_days': config['preparation_days_min'],
        'preparation_max_days': config['preparation_days_max'],
        'transit_min_days': zone['delivery_days_min'], 'transit_max_days': zone['delivery_days_max'],
        'delivery_min_days': config['preparation_days_min'] + zone['delivery_days_min'],
        'delivery_max_days': config['preparation_days_max'] + zone['delivery_days_max'],
        'config_revision': config['revision'], 'zone_revision': zone['revision'],
    }


def _money(value):
    return format(value, '.2f').replace('.', ',') + ' €'


def legal_delivery(config=None):
    config = configuration() if config is None else config
    if not config['managed']:
        legacy = getattr(settings, 'SHOP_LEGAL', {})
        return legacy.get('delivery', '') if isinstance(legacy, dict) else ''
    if not config['enabled']:
        return 'La livraison et la vente en ligne sont temporairement indisponibles.'
    lines = []
    if config['carrier_name']:
        lines.append(config['service_name'] + ' par ' + config['carrier_name'] + '.')
    if _days_ready(config['preparation_days_min'], config['preparation_days_max']):
        lines.append(f"Préparation : {config['preparation_days_min']} à {config['preparation_days_max']} jours ouvrés.")
    for zone in config['zones']:
        if not _zone_ready(zone):
            continue
        text = f"{zone['title']} — {zone['country_summary']} : {_money(zone['price'])} par commande."
        if config['free_shipping_enabled'] and zone['free_shipping_eligible']:
            if config['free_shipping_threshold'] == 0:
                text += ' Livraison offerte sans minimum d’articles.'
            else:
                text += ' Livraison offerte dès ' + _money(config['free_shipping_threshold']) + ' d’articles, avant application d’un code client.'
        text += f" Acheminement : {zone['delivery_days_min']} à {zone['delivery_days_max']} jours ouvrés après préparation."
        if _days_ready(config['preparation_days_min'], config['preparation_days_max']):
            text += f" Délai total annoncé : {config['preparation_days_min'] + zone['delivery_days_min']} à {config['preparation_days_max'] + zone['delivery_days_max']} jours ouvrés."
        lines.append(text)
    if not lines:
        lines.append('Les destinations, tarifs et délais de livraison restent à compléter avant l’ouverture des ventes.')
    lines.append('Seuls les pays proposés lors de la commande sont livrables. Les frais exacts et le total sont affichés avant paiement.')
    if config['public_notice']:
        lines.append(config['public_notice'])
    return '\n'.join(lines)
