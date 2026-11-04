# Aide intégrée à l’espace vendeuse

L’application `aide` fournit des procédures courtes, vérifiées contre les écrans
existants. Aucun modèle ni changement des données commerciales n’est nécessaire.

## Accès

- Un encart repliable « Aide pour cette page » est ajouté par le gabarit commun
  sur les pages de gestion. Il reste hors des formulaires et n’exécute aucun script.
- Les liens de cet encart ouvrent un nouvel onglet pour conserver la saisie.
- `/gestion/aide/` propose la recherche et les rubriques repliables ; une recherche
  ouvre les rubriques correspondantes et privilégie les titres pertinents.
- `/gestion/aide/<identifiant>/` donne une réponse avec étapes, remarques et
  liens vers les outils autorisés. Les liens de retour ont une destination stable.
- La page « Tous les outils » contient également un accès au guide.

Les accès suivent les rôles catalogue et service client, ainsi que la vérification
du compte déjà en vigueur. Les vues du guide sont privées, non mises en cache,
non indexables et limitées à GET. Les clients et aperçus du site public ne reçoivent
pas l’encart. Les articles et liens réservés au mode test sont filtrés.

## Contenu et maintenance

Les 109 réponses sont réparties dans `aide/content/` :

- `general.py` : prise en main et statistiques ;
- `catalog.py` : fiches, déclinaisons, rubriques, soldes, référencement et stocks ;
- `site.py` : accueil, pages, FAQ, annonces, coordonnées, horaires et livraison ;
- `service.py` : clients, SAV, avoirs, commandes, retours, comptabilité et alertes.

Pour ajouter une page, compléter `PAGE_HELP` dans le module correspondant avec
des identifiants d’articles. Utiliser `PAGE_HELP_VARIANTS` lorsque plusieurs
écrans partagent la même route mais ont des paramètres différents. Ajouter les
routes sans écran (JSON, téléchargement, redirection) à `NON_PAGE_ROUTES` avec
une raison ; les routes pouvant réafficher une erreur HTML gardent une aide.

Une action d’aide doit uniquement ouvrir une page GET. Ne jamais pointer vers
une opération de paiement, suppression, envoi ou autre mutation. Renseigner le
droit nécessaire sur chaque article et chaque lien ; un lien peut demander un
droit différent de celui de l’article. Ne pas décrire un bouton ou un parcours
inexistant. Les procédures métier sont à actualiser lors du changement de l’écran.

## Vérifications

`python manage.py test aide tableau.test_home core.test_seller_navigation`

Les contrôles couvrent toutes les routes nommées sous `/gestion/`, les références
des articles, les liens, le rendu de chaque réponse, les permissions, les pages
publiques et aperçus, la recherche, les filtres et la confidentialité. Ils doivent
être exécutés avec une base de test isolée et l’envoi d’e-mails désactivé.

Compléter par un contrôle visuel des encarts et du guide à 320, 390, 768 et
1440 pixels, ainsi qu’une saisie non enregistrée conservée pendant l’ouverture
de l’aide. Les accordéons natifs fonctionnent également au clavier.
