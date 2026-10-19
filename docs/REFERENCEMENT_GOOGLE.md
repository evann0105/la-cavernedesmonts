# Référencement Google — La Caverne des Monts

Le site prépare automatiquement le référencement de chaque fiche produit, y compris les articles déjà créés. Il n’est pas nécessaire de remplir une liste de mots-clés à chaque ajout. L’automatisation utilise les renseignements réels du catalogue ; elle ne peut pas remplacer une marque, une photo ou une caractéristique inconnue.

## Ce qui se met à jour automatiquement

Pour les produits existants et futurs, le site construit un titre adapté, une description destinée aux moteurs et un résumé lisible à partir du nom, de la marque renseignée, du modèle, de la description, des couleurs, des tailles et de la composition. La description rédigée par la boutique reste conservée. Une correction de la fiche est répercutée dans le référencement au prochain affichage.

Exemple : une fiche nommée « Veste polaire femme verte », de marque « Peak Mountain », peut couvrir des recherches naturelles telles que « polaire Peak Mountain », « veste polaire femme Peak Mountain » et « polaire femme verte ». Les expressions sont tirées des faits enregistrés ; aucune promesse d’imperméabilité, de fabrication française ou de certification n’est ajoutée sans information correspondante.

Dans l’administration, la rubrique **Référencement Google**, sous **Catalogue** dans **Mon compte**, est accessible à `/gestion/referencement/`. Elle permet de consulter l’état du catalogue et les points à compléter. La section **Référencement automatique** d’une fiche présente son aperçu. Les expressions proposées servent à comprendre les recherches pertinentes. Le site ne remplit pas une balise `meta keywords` : Google ne l’utilise pas pour son classement. Des titres précis et du contenu utile sont plus pertinents qu’une accumulation répétitive de mots-clés. [Consignes Google sur les titres](https://developers.google.com/search/docs/appearance/title-link), [balises prises en charge](https://developers.google.com/search/docs/crawling-indexing/special-tags), [descriptions](https://developers.google.com/search/docs/appearance/snippet).

## Prix, couleurs, tailles et données produit

Chaque fiche publiée contient des données structurées `Product`, ou `ProductGroup` lorsque plusieurs déclinaisons sont enregistrées. Elles décrivent les couleurs et tailles réelles, des identifiants internes stables et des liens qui présélectionnent précisément la variante concernée. Les photos des coloris sont associées à leur couleur connue.

Lorsqu’un produit peut effectivement être acheté en ligne et possède un stock compté, les offres reprennent son prix actuel, les réductions programmées en cours et le stock disponible après réservations. Une promotion terminée retrouve le prix habituel. Un stock inconnu, un produit sans variante achetable ou la désactivation des ventes ne sont pas présentés comme une disponibilité d’achat confirmée.

Aucun EAN, fabricant, avis produit, note, délai de livraison ou frais de retour n’est inventé. Les avis Google portant sur le magasin ne deviennent pas des avis sur chaque produit. Ce balisage aide Google à comprendre le catalogue ; son affichage enrichi reste soumis aux règles et aux décisions de Google. [Variantes produit](https://developers.google.com/search/docs/appearance/structured-data/product-variants), [fiches marchandes](https://developers.google.com/search/docs/appearance/structured-data/merchant-listing).

## Organisation du site pour les moteurs

- Les pages publiques possèdent un titre, une description et une URL canonique cohérente.
- Les collections utiles, les fiches produit et le plan du site permettent de découvrir les articles.
- `/sitemap.xml` recense les produits publiés et les pages publiques retenues. Les nouvelles fiches sont incluses automatiquement ; les brouillons en sont exclus.
- `/robots.txt` indique la politique d’exploration et l’emplacement du sitemap lorsque l’indexation est activée.
- Les recherches internes et combinaisons arbitraires de filtres évitent de créer des séries de pages indexables en double.
- L’administration, le compte, le panier, les commandes, les factures, les retours, les liens privés et les réponses techniques reçoivent des consignes de non-indexation. Les protections d’accès continuent de s’appliquer : `robots.txt` ne remplace jamais une authentification.
- Les photographies publiques des produits restent accessibles à l’indexation des images en production.
- Les données de l’établissement reprennent les coordonnées publiques et les horaires publiés, y compris une période exceptionnelle active, sans note ni coordonnées géographiques inventées.

Le sitemap est un signal de découverte ; il ne garantit ni l’exploration ni l’indexation de toutes les URL. [Sitemaps](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap), [établissements locaux](https://developers.google.com/search/docs/appearance/structured-data/local-business).

## Mise en ligne sur le domaine définitif

La cible est `https://la-cavernedesmonts.fr`. Le site local reste volontairement non indexable. Après configuration du domaine, du certificat HTTPS et du serveur public, définir dans l’environnement de production :

```dotenv
DEBUG=False
SITE_URL=https://la-cavernedesmonts.fr
ALLOWED_HOSTS=la-cavernedesmonts.fr,www.la-cavernedesmonts.fr
SEO_INDEXING_ENABLED=True
```

Choisir une seule version officielle du domaine et faire rediriger durablement les autres versions vers celle-ci, notamment HTTP et, si utilisé, `www`. Ne pas recopier les secrets de production dans GitHub. Les paramètres d’ouverture des ventes, de paiement et de conformité sont indépendants de l’indexation et doivent être validés séparément.

Après déploiement, vérifier les pages publiques sans connexion, leur URL canonique, `/robots.txt`, le contenu de `/sitemap.xml` et quelques fiches dans le [test Google des résultats enrichis](https://search.google.com/test/rich-results).

## Actions à réaliser dans les comptes Google

1. Ajouter ou récupérer la propriété du domaine `la-cavernedesmonts.fr` dans Google Search Console avec le compte autorisé par Élodie. La propriété de domaine se valide auprès du fournisseur DNS ; conserver le jeton fourni par Google. Aucun compte n’a été validé automatiquement par l’ajout du code au site. [Validation de propriété](https://support.google.com/webmasters/answer/9008080?hl=fr).
2. Soumettre `https://la-cavernedesmonts.fr/sitemap.xml`, puis inspecter l’accueil, une catégorie et plusieurs fiches représentatives. Suivre les pages indexées, les erreurs et les requêtes réellement utilisées. La publication d’une fiche ne signifie pas son apparition immédiate dans Google. [Envoi du sitemap](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap).
3. Vérifier la véritable fiche Google Business Profile du magasin : accès propriétaire, nom réel, catégorie d’activité, adresse, téléphone, horaires et lien vers le site définitif. Éviter de créer un doublon si une fiche existe déjà. [Consignes pour les établissements Google](https://support.google.com/business/answer/3038177?hl=fr).
4. En complément, envisager Google Merchant Center après l’ouverture effective des ventes et la validation du catalogue, de la livraison et des retours. La connexion du compte et l’éventuel flux produit constituent une étape distincte ; ils ne sont pas créés par le seul balisage des fiches. [Options de présentation des produits](https://developers.google.com/search/docs/appearance/structured-data/merchant-listing).

## Renseignements utiles à garder exacts

Pour chaque produit, Élodie renseigne les informations commerciales habituelles : nom précis, marque lorsqu’elle existe, modèle si connu, catégorie correcte, caractéristiques vérifiées, photo principale, coloris, tailles, prix et quantités. La composition et les informations fabricant doivent provenir de l’étiquette ou d’une source fiable correspondant au modèle exact. L’aperçu et les alertes de l’administration aident à repérer les fiches trop incomplètes.

Il n’existe aucun réglage permettant de garantir la première place sur « polaire » ou « polaire Peak Mountain ». La concurrence, la pertinence de la fiche, la qualité du site et les décisions de Google interviennent. Cette préparation donne au catalogue une base cohérente et automatisée ; les résultats se suivent ensuite dans Search Console. [Guide officiel de démarrage SEO](https://developers.google.com/search/docs/fundamentals/seo-starter-guide?hl=fr).
