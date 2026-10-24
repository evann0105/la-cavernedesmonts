"""Finite, plain-text fields for the merchant's public information pages."""
from django.core.exceptions import ValidationError


def field(label, default, limit=200, *, paragraph=False, required=True):
    return {'label': label, 'default': default, 'max_length': limit,
            'paragraph': paragraph, 'required': required}


PAGE_SPECS = {
    'about': {
        'title': 'À propos', 'description': 'Racontez la boutique et choisissez sa photo de présentation.',
        'public_route': 'espace:about',
        'fields': {
            'page_title': field('Titre de la page', 'À propos', 100),
            'intro': field('Phrase d’introduction', 'Un peu de montagne. Beaucoup de souvenirs.', 300),
            'eyebrow': field('Petit titre au-dessus de la présentation', 'LÉLEX · JURA', 100),
            'heading': field('Titre de la présentation', 'Le goût des choses simples.', 200),
            'body': field('Présentation', 'La Caverne des Monts, c’est une boutique à Lélex et une envie : partager un peu de l’esprit montagne, bien au-delà des vacances.\n\nDes vêtements pour toute la famille, une matière douce, une petite attention à offrir. Nous aimons les trouvailles qui accompagnent les moments heureux.\n\nEn boutique comme à distance, le plaisir reste le même : échanger et vous aider à choisir.', 6000, paragraph=True),
            'button_label': field('Texte du bouton vers le magasin', 'Découvrir notre magasin', 100),
            'image_alt': field('Description de la photo', 'La façade de notre boutique à Lélex', 200),
        },
        'panels': (
            ('En-tête de la page', ('page_title', 'intro')),
            ('L’histoire de la boutique', ('eyebrow', 'heading', 'body', 'button_label')),
            ('Photo de présentation', ('image', 'image_alt')),
        ),
    },
    'store': {
        'title': 'Présentation du magasin',
        'description': 'Présentez le magasin, ses conseils et l’accueil sur place.',
        'public_route': 'core:location',
        'fields': {
            'page_title': field('Titre de la page pour le navigateur', 'Notre magasin à Lélex', 100),
            'title_line1': field('Grand titre · première ligne', 'La montagne nous rassemble.', 180),
            'title_line2': field('Grand titre · deuxième ligne', 'Entrez, vous êtes chez nous.', 180),
            'intro': field('Introduction', 'Une vitrine, des découvertes et le plaisir de prendre le temps. À Lélex, notre boutique prolonge vos moments au grand air.', 1200, paragraph=True),
            'photo_caption': field('Légende de la photo', 'Une vraie boutique. Une rencontre.', 200),
            'story_kicker': field('Petit titre de la présentation', 'LE PLAISIR DE SE RENCONTRER', 100),
            'story_heading': field('Titre de la présentation', 'Les belles trouvailles commencent par un échange.', 200),
            'story_body': field('Présentation', 'Toucher une matière, essayer une coupe, hésiter entre deux couleurs. Ici, on prend le temps de trouver ce qui vous ressemble.', 4000, paragraph=True),
            'value1_title': field('Premier point fort · titre', 'Pour toute la famille', 120),
            'value1_body': field('Premier point fort · texte', 'Des vêtements pour les petits et les grands, et des attentions à partager.', 1200, paragraph=True),
            'value2_title': field('Deuxième point fort · titre', 'Un conseil, simplement', 120),
            'value2_body': field('Deuxième point fort · texte', 'Une question sur une taille ou une envie de cadeau ? Parlons-en ensemble.', 1200, paragraph=True),
            'visit_kicker': field('Petit titre des informations pratiques', 'ON SE RETROUVE À LÉLEX', 100),
            'visit_heading': field('Titre des informations pratiques', 'Votre prochaine halte.', 200),
            'before_visit_text': field('Conseil avant de venir', 'Appelez-nous pour connaître les horaires du moment et préparer votre passage en boutique.', 1200, paragraph=True),
            'closing_text': field('Phrase de fin', 'Un peu de montagne. Beaucoup de souvenirs.', 300),
            'image_alt': field('Description de la photo', 'La façade de notre boutique à Lélex', 200),
        },
        'panels': (
            ('Introduction', ('page_title', 'title_line1', 'title_line2', 'intro')),
            ('Photo du magasin', ('image', 'image_alt', 'photo_caption')),
            ('La rencontre en boutique', ('story_kicker', 'story_heading', 'story_body')),
            ('Les points forts', ('value1_title', 'value1_body', 'value2_title', 'value2_body')),
            ('Préparer sa visite', ('visit_kicker', 'visit_heading', 'before_visit_text', 'closing_text')),
        ),
    },
}

TARGET_ROUTES = {
    'boutique': 'core:location', 'contact': 'espace:contact',
    'livraison': 'boutique:shipping_information', 'faq': 'editorial:faq',
    'commandes': 'espace:orders', 'retours': 'retours:entry',
}
TARGET_CHOICES = (('', 'Aucun lien'), ('boutique', 'Notre magasin'),
                  ('contact', 'Contact'), ('livraison', 'Livraison'),
                  ('faq', 'Questions fréquentes'), ('commandes', 'Mes commandes'),
                  ('retours', 'Demander un retour'))
ANNOUNCEMENT_TARGET_CHOICES = TARGET_CHOICES[:5]
KIND_CHOICES = (('information', 'Information'), ('fermeture', 'Fermeture'),
                ('vacances', 'Vacances'), ('expedition', 'Retard d’expédition'))
PRIORITY_CHOICES = ((0, 'Normale'), (10, 'Importante'), (20, 'Prioritaire'))


def plain_text(value, *, limit, required=True):
    if not isinstance(value, str):
        raise ValidationError('Renseignez un texte valide.')
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise ValidationError('Renseignez un texte de %(limit)s caractères maximum.', params={'limit': limit})
    if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
        raise ValidationError('Ce texte contient un caractère non autorisé.')
    return value


def validate_page_data(key, data):
    if key not in PAGE_SPECS or not isinstance(data, dict):
        raise ValidationError('La page ou son contenu est invalide.')
    fields = PAGE_SPECS[key]['fields']
    if set(data) - set(fields):
        raise ValidationError('Le contenu comporte un champ qui ne correspond pas à cette page.')
    for name, value in data.items():
        spec = fields[name]
        plain_text(value, limit=spec['max_length'], required=spec['required'])


def target_url(target):
    from django.urls import reverse
    route = TARGET_ROUTES.get(target)
    return reverse(route) if route else ''
