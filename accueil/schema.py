"""Closed editorial schema; French defaults reproduce the existing storefront."""


def text(label, default, *, required=True, max_length=160, kind='text'):
    return {'label': label, 'default': default, 'kind': kind,
            'required': required, 'max_length': max_length}


def paragraph(label, default, *, required=False, max_length=800):
    return text(label, default, required=required, max_length=max_length, kind='textarea')


def target(label, default):
    return text(label, default, kind='target', max_length=40)


TARGET_CHOICES = (
    ('catalogue', 'Toute la collection'), ('femmes', 'Collection Femme'),
    ('hommes', 'Collection Homme'), ('enfants', 'Collection Enfant'),
    ('accessoires', 'Petits bonheurs et accessoires'),
    ('accessoires_bebe', 'Accessoires bébé'), ('polaires_bebe', 'Polaires bébé'),
    ('offres_speciales', 'Collection Offres spéciales'),
    ('promotions', 'Promotions'), ('nouveautes', 'Nouveautés'),
    ('boutique', 'Notre magasin'), ('contact', 'Nous contacter'),
    ('telephone', 'Appeler la boutique'), ('email', 'Écrire à la boutique'),
    ('meilleures_ventes', 'La sélection de la boutique'),
)

SECTION_SPECS = (
    {'key': 'hero', 'title': 'Grande introduction',
     'description': 'Le grand titre, les photos et le premier bouton de l’accueil.',
     'images': ['hero_image', 'hero_mobile_image'], 'fields': {
         'kicker': text('Petit texte au-dessus du titre', 'LA CAVERNE DES MONTS · LÉLEX'),
         'title_line1': text('Grand titre · première ligne', 'Un peu plus dehors.'),
         'title_line2': text('Grand titre · deuxième ligne', 'Un peu plus', required=False),
         'title_emphasis': text('Grand titre · mots mis en valeur', 'vivant.', required=False),
         'description': paragraph('Présentation', 'Vêtements, accessoires & essentiels outdoor pour toute la famille, à Lélex.'),
         'advice': paragraph('Conseil', 'Une taille, une matière, un cadeau : nous vous aidons à faire le bon choix.'),
         'button_label': text('Texte du bouton', 'Voir la collection', max_length=80),
         'button_target': target('Destination du bouton', 'catalogue'),
         'scroll_label': text('Invitation à descendre dans la page', 'Laissez-vous inspirer', required=False),
         'image_alt': text('Description des photos d’introduction', 'Des randonneurs au pied des montagnes, sur un sentier fleuri', max_length=250),
     }},
    {'key': 'reassurance', 'title': 'Les trois points forts',
     'description': 'Les petits repères juste sous la grande photo.', 'images': [], 'fields': {
         'aria_label': text('Titre accessible du bloc', 'Une boutique à votre écoute'),
         'store_title': text('Premier point · titre', 'Une vraie boutique'),
         'store_description': text('Premier point · précision', 'À Lélex, au cœur du Jura'),
         'store_target': target('Premier point · destination', 'boutique'),
         'advice_title': text('Deuxième point · titre', 'Des conseils humains'),
         'advice_description': text('Deuxième point · précision', 'Une question ? Parlons-en.'),
         'advice_target': target('Deuxième point · destination', 'telephone'),
         'family_title': text('Troisième point · titre', 'Pour toute la famille'),
         'family_description': text('Troisième point · précision', 'Les petits comme les grands'),
         'family_target': target('Troisième point · destination', 'catalogue'),
     }},
    {'key': 'manifesto', 'title': 'L’esprit de la boutique',
     'description': 'Le texte d’accueil qui raconte votre univers.', 'images': [], 'fields': {
         'kicker': text('Petit titre', 'L’essentiel, tout simplement'),
         'title_line1': text('Titre · première ligne', 'On vient pour la montagne.'),
         'title_line2': text('Titre · deuxième ligne', 'On reste pour', required=False),
         'title_emphasis': text('Titre · mots mis en valeur', 'ce qu’on y ressent.', required=False),
         'description': paragraph('Présentation', 'Une bouffée d’air frais. Une polaire toute douce. Une attention pour quelqu’un qu’on aime.'),
         'description_emphasis': paragraph('Dernière phrase', 'Les belles journées commencent par les choses simples.'),
         'button_label': text('Texte du lien', 'Rencontrer notre boutique', max_length=80),
         'button_target': target('Destination du lien', 'boutique'),
     }},
    {'key': 'worlds', 'title': 'Les trois univers',
     'description': 'Les titres des cartes Femme, Homme et Enfant. Les produits illustrant ces cartes se choisissent dans leur page dédiée.',
     'images': [], 'fields': {
         'kicker': text('Petit titre', 'À CHACUN SON ÉCHAPPÉE'),
         'title': text('Titre du bloc', 'Votre envie du moment.'),
         'note': text('Précision', 'Trois univers. Le même esprit.', required=False),
         'women_title': text('Carte Femme · titre', 'Bien dans sa nature.'),
         'men_title': text('Carte Homme · titre', 'L’appel du dehors.'),
         'kids_title': text('Carte Enfant · titre', 'Grandir au grand air.'),
         'button_label': text('Début du texte des liens', 'Explorer la collection', max_length=80),
     }},
    {'key': 'feature', 'title': 'Photo et invitation au grand air',
     'description': 'Le grand bloc d’ambiance avec son texte et sa photo.',
     'images': ['feature_image'], 'fields': {
         'kicker': text('Petit titre', 'Dehors, on respire.'),
         'title_line1': text('Titre · première ligne', 'La journée est belle.'),
         'title_line2': text('Titre · deuxième ligne', 'Profitez-en.', required=False),
         'description': paragraph('Présentation', 'Partir sans regarder l’heure. Prendre le chemin le plus long. Retrouver le plaisir d’être dehors, tout simplement.'),
         'button_label': text('Texte du bouton', 'Voir la collection', max_length=80),
         'button_target': target('Destination du bouton', 'catalogue'),
         'footnote': text('Phrase sous le bouton', 'Un peu de montagne. Beaucoup de souvenirs.', required=False),
         'image_alt': text('Description de la photo', 'Une randonnée en montagne sous un ciel bleu', max_length=250),
         'photo_note': text('Phrase sur la photo', 'Le bonheur n’est jamais très loin.', required=False),
     }},
    {'key': 'selection', 'title': 'Les produits mis en avant',
     'description': 'Le titre et le bouton des essentiels. Leur ordre se règle dans « Produits mis en avant ».',
     'images': [], 'fields': {
         'kicker': text('Petit titre', 'À EMPORTER AVEC SOI'),
         'title': text('Titre du bloc', 'Les essentiels de la montagne.'),
         'button_label': text('Texte du bouton', 'Voir la collection', max_length=80),
         'button_target': target('Destination du bouton', 'catalogue'),
     }},
    {'key': 'family', 'title': 'Bébé et petites attentions',
     'description': 'Les trois cartes pour les accessoires bébé, les polaires bébé et les offres.',
     'images': ['family_accessories_image', 'family_fleece_image', 'family_gifts_image'], 'fields': {
         'kicker': text('Petit titre', 'LE BONHEUR SE PARTAGE'),
         'title_line1': text('Titre · première ligne', 'Tout petit.'),
         'title_emphasis': text('Titre · deuxième ligne', 'Déjà tout un monde.', required=False),
         'description': paragraph('Présentation', 'Les joues fraîches, les petites mains au chaud, les câlins au retour. L’esprit montagne se partage dès les premiers instants.'),
         'button_label': text('Texte du lien', 'Un conseil ? Parlons-en', max_length=80),
         'button_target': target('Destination du lien', 'boutique'),
         'aria_label': text('Titre accessible des cartes', 'Nos collections bébé et offres'),
         'accessories_label': text('Carte accessoires · petit titre', 'ACCESSOIRES BÉBÉ'),
         'accessories_title': text('Carte accessoires · titre', 'Petits détails, grands câlins.'),
         'accessories_target': target('Carte accessoires · destination', 'accessoires_bebe'),
         'accessories_image_alt': text('Description de la photo accessoires', 'Un bébé en bonnet sauge et maille crème dans un chalet lumineux', max_length=250),
         'fleece_label': text('Carte polaires · petit titre', 'POLAIRES BÉBÉ'),
         'fleece_title': text('Carte polaires · titre', 'Ses premiers cocons.'),
         'fleece_target': target('Carte polaires · destination', 'polaires_bebe'),
         'fleece_image_alt': text('Description de la photo polaires', 'Un bébé en polaire crème blotti dans les bras de sa maman, face aux montagnes', max_length=250),
         'gifts_label': text('Carte offres · petit titre', 'OFFRES SPÉCIALES'),
         'gifts_title': text('Carte offres · titre', 'Une occasion de faire plaisir.'),
         'gifts_target': target('Carte offres · destination', 'offres_speciales'),
         'gifts_image_alt': text('Description de la photo offres', 'Un bébé et sa maman découvrent un cadeau dans une ambiance chalet', max_length=250),
     }},
    {'key': 'store', 'title': 'Présentation du magasin',
     'description': 'La photo et l’invitation à vous rendre visite à Lélex.',
     'images': ['store_image'], 'fields': {
         'kicker': text('Petit titre', 'RENDEZ-VOUS À LÉLEX'),
         'title': text('Titre du bloc', 'La montagne nous rassemble.'),
         'description': paragraph('Présentation', 'Derrière cette vitrine, des découvertes et le plaisir de vous conseiller. Poussez la porte, prenez votre temps.'),
         'button_label': text('Texte du bouton', 'Découvrir notre magasin', max_length=80),
         'button_target': target('Destination du bouton', 'boutique'),
         'image_alt': text('Description de la photo', 'La façade de notre boutique à Lélex', max_length=250),
     }},
    {'key': 'reviews', 'title': 'Présentation des avis',
     'description': 'Le titre et l’introduction uniquement. Les avis cités et les précisions sur leur origine restent conservés.',
     'images': [], 'fields': {
         'title': text('Titre du bloc', 'Quelques mots de nos clients.'),
         'intro': paragraph('Introduction', 'Sélection d’avis sur la boutique de Lélex.'),
     }},
    {'key': 'contact', 'title': 'Dernière invitation à vous contacter',
     'description': 'Les mots affichés tout en bas de l’accueil et leur bouton de contact.',
     'images': [], 'fields': {
         'kicker': text('Petit titre', 'LA CAVERNE DES MONTS · LÉLEX'),
         'title_line1': text('Titre · première ligne', 'De vraies personnes.'),
         'title_line2': text('Titre · deuxième ligne', 'De bons conseils.', required=False),
         'description': paragraph('Première phrase', 'Une hésitation sur une taille ? Une idée cadeau ?'),
         'description_line2': paragraph('Deuxième phrase', 'On prend le temps de vous répondre.'),
         'button_label': text('Texte du bouton (facultatif)', '', required=False, max_length=80),
         'button_target': target('Destination du bouton', 'telephone'),
     }},
)

SECTIONS = {section['key']: section for section in SECTION_SPECS}
OPTIONAL_SECTIONS = tuple(key for key in SECTIONS if key != 'hero')
IMAGE_FIELDS = tuple(name for section in SECTION_SPECS for name in section['images'])
IMAGE_LABELS = {
    'hero_image': 'Photo principale', 'hero_mobile_image': 'Photo pour téléphone',
    'feature_image': 'Photo du grand air', 'family_accessories_image': 'Photo accessoires bébé',
    'family_fleece_image': 'Photo polaires bébé', 'family_gifts_image': 'Photo petites attentions',
    'store_image': 'Photo du magasin',
}
IMAGE_DEFAULTS = {
    'hero_image': 'core/img/hero/montagne-1536.webp',
    'hero_mobile_image': 'core/img/hero/montagne-mobile-960.webp',
    'feature_image': 'core/img/feature/randonnee-1536.webp',
    'family_accessories_image': 'core/img/bb/chalet/accessoires-768.webp',
    'family_fleece_image': 'core/img/bb/chalet/polaires-768.webp',
    'family_gifts_image': 'core/img/bb/chalet/attentions-768.webp',
    'store_image': 'core/img/boutique/lelex-2400.jpg',
}
SECTION_ANCHORS = {
    'hero': 'summit-title', 'reassurance': 'home-reassurance', 'manifesto': 'instants',
    'worlds': 'home-worlds', 'feature': 'home-feature', 'selection': 'home-selection',
    'family': 'petites-attentions', 'store': 'home-store',
    'reviews': 'home-reviews', 'contact': 'home-contact',
}
