ARTICLES = (
    {
        'id': 'retrouver-une-fonction', 'category': 'quotidien', 'permission': 'seller',
        'title': 'Où retrouver les outils de la boutique ?',
        'summary': 'L’espace vendeuse rassemble les tâches du jour et les outils classés par activité.',
        'steps': (
            'Ouvrez « À faire aujourd’hui » pour voir les commandes et demandes qui restent à traiter, même si elles datent des jours précédents.',
            'Cliquez sur une carte pour ouvrir la liste correspondante.',
            'Ouvrez « Tous les outils » pour retrouver le catalogue, les contenus, les réglages et les documents auxquels votre compte a accès.',
        ),
        'notes': ('« Mon compte personnel » concerne vos propres achats. « Voir la boutique » ouvre le site tel que les clients le voient.',),
        'actions': ({'label': 'Ouvrir les tâches du jour', 'view': 'tableau:home', 'permission': 'seller'},
                    {'label': 'Retrouver tous les outils', 'view': 'tableau:tools', 'permission': 'seller'}),
    },
    {
        'id': 'comprendre-les-alertes-du-jour', 'category': 'quotidien', 'permission': 'seller',
        'title': 'Que signifient les chiffres de l’accueil vendeuse ?',
        'summary': 'Chaque chiffre indique une liste à consulter, pas une action déjà effectuée.',
        'steps': (
            'Les cartes colorées signalent des éléments en attente. Ouvrez la carte concernée pour consulter les détails.',
            'Dans « Les prochains colis », commencez par les commandes les plus anciennes. Une mention « À vérifier » signale un blocage à résoudre avant l’expédition.',
            'Consultez également les annulations à vérifier, les rappels SAV, les retours, les messages en échec et les stocks à surveiller, selon les accès de votre compte.',
        ),
        'notes': ('Une commande peut être concernée par plusieurs alertes : les chiffres des cartes ne doivent pas être additionnés.',
                  'Ouvrir cet accueil ne modifie aucune commande et n’envoie aucun message.'),
        'actions': ({'label': 'Voir les tâches du jour', 'view': 'tableau:home', 'permission': 'seller'},),
    },
    {
        'id': 'utiliser-aide', 'category': 'quotidien', 'permission': 'seller',
        'title': 'Comment trouver une réponse sans quitter mon travail ?',
        'summary': 'L’aide de chaque page peut s’ouvrir pendant que vous remplissez un formulaire.',
        'steps': (
            'Ouvrez « Aide pour cette page », puis la question qui correspond à votre besoin.',
            'Lisez les étapes, refermez l’aide et poursuivez votre saisie. L’ouverture de l’aide n’enregistre ni ne réinitialise votre formulaire.',
            'Pour un autre sujet, ouvrez le guide complet et recherchez une action, par exemple « ajouter une taille » ou « corriger un stock ».',
        ),
        'notes': ('Enregistrez votre formulaire avant de suivre un lien vers une autre page.',),
        'actions': ({'label': 'Ouvrir le guide complet', 'view': 'aide:index', 'permission': 'seller'},),
    },
    {
        'id': 'consulter-statistiques', 'category': 'quotidien', 'permission': 'catalog',
        'title': 'Comment lire les statistiques de vente ?',
        'summary': 'Choisissez une période pour consulter les indicateurs et les produits vendus.',
        'steps': (
            'Choisissez l’année et le mois, ou « Année entière », puis cliquez sur « Afficher les ventes ».',
            'Lisez le libellé de chaque indicateur et les éventuelles informations sur les remboursements.',
            'Pour préparer les documents du comptable, utilisez « Comptabilité et exports », si votre compte dispose de cet accès.',
        ),
        'actions': ({'label': 'Consulter les statistiques', 'view': 'tableau:dashboard', 'permission': 'catalog'},
                    {'label': 'Ouvrir la comptabilité', 'view': 'facturation:accounting', 'permission': 'service'}),
    },
    {
        'id': 'consulter-mails-test', 'category': 'alertes', 'permission': 'catalog', 'test_only': True,
        'title': 'Pourquoi un e-mail de test n’arrive-t-il pas dans ma boîte ?',
        'summary': 'La boîte de test conserve les messages générés pendant les essais.',
        'steps': (
            'Ouvrez « Boîte e-mail de test » et retrouvez le message grâce à son destinataire et à son objet.',
            'Consultez son contenu pour vérifier le texte et le code éventuel.',
            'Pour les envois réels et leurs échecs, consultez « E-mails et alertes » lorsque cette fonction est accessible à votre compte.',
        ),
        'notes': ('Un message visible dans la boîte de test ne prouve pas qu’un e-mail a été livré à son destinataire. L’envoi réel dépend de la configuration du mail professionnel.',),
        'actions': ({'label': 'Ouvrir la boîte de test', 'view': 'test_email_inbox', 'permission': 'catalog', 'test_only': True},),
    },
)

PAGE_HELP = {
    'tableau:home': ('comprendre-les-alertes-du-jour', 'boutique-suivre-dupliquer-pauses', 'retrouver-une-fonction', 'utiliser-aide'),
    'tableau:tools': ('retrouver-une-fonction', 'utiliser-aide'),
    'tableau:dashboard': ('consulter-statistiques', 'retrouver-une-fonction'),
    'test_email_inbox': ('consulter-mails-test',),
    'aide:index': ('utiliser-aide',),
    'aide:detail': ('utiliser-aide',),
}
